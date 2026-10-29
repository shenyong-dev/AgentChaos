"""Read-only, owner-bound comparisons from frozen run/case/SQL/event evidence."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.evaluation.service import load_evidence
from app.executors.service import RunService
from app.metamorphic.catalog import frozen_relations
from app.metamorphic.engine import comparator_manifest, compare_relation, observe
from app.models import TestExecution
from app.schemas.metamorphic import RelationCatalog


class RelationService:
    def __init__(self, engine):
        self.engine = engine

    def read(self, owner, run_id):
        with Session(self.engine) as session, session.begin():
            run = RunService.owned_run(session, owner, run_id, lock=True)
            if canonical_hash(run.manifest_json) != run.manifest_hash:
                raise AgentError("relation_integrity_error", "运行清单哈希不符。", 503)
            meta = run.manifest_json.get("metamorphic")
            if not meta:
                return {
                    "run_id": str(run_id),
                    "catalog": None,
                    "items": [],
                    "members": [],
                    "summary": None,
                    "comparator": comparator_manifest(),
                }
            if frozen_relations(run.manifest_json["cases"]) != meta:
                raise AgentError("relation_integrity_error", "关系清单不匹配已冻结用例。", 503)
            catalog = RelationCatalog.model_validate(meta["catalog"])
            rows = list(
                session.scalars(
                    select(TestExecution)
                    .where(TestExecution.run_id == run.id)
                    .order_by(TestExecution.id)
                    .with_for_update()
                )
            )
            cases = {c["case_revision_id"]: c for c in run.manifest_json["cases"]}
            observed, items, hashes = [], [], []
            for repeat in range(1, run.manifest_json["repetitions"] + 1):
                members, seeds = {}, set()
                for member in catalog.members:
                    cid = meta["member_cases"][member.key]
                    matches = [
                        r for r in rows if r.case_revision_id == cid and r.repeat_no == repeat
                    ]
                    if len(matches) != 1:
                        raise AgentError("relation_integrity_error", "缺少唯一关系成员执行。", 503)
                    row = matches[0]
                    if row.input_snapshot_json != cases[cid]:
                        raise AgentError(
                            "relation_integrity_error", "执行输入与冻结成员不符。", 503
                        )
                    seeds.add(row.seed)
                    bundle = load_evidence(session, run, row)
                    members[member.key] = observe(member, bundle, row.status)
                    hashes.append(
                        canonical_hash(
                            {
                                "input": row.input_snapshot_json,
                                "before": bundle.before,
                                "after": bundle.after,
                                "output": bundle.output,
                                "events": bundle.events,
                                "status": row.status,
                                "seed": row.seed,
                                "issues": bundle.issues,
                                "integrity_errors": bundle.integrity_errors,
                            }
                        )
                    )
                observed += [{**v, "repeat": repeat} for v in members.values()]
                for relation in catalog.relations:
                    items.append(
                        {
                            **compare_relation(
                                relation,
                                members[relation.base],
                                members[relation.variant],
                                same_conditions=seeds == {run.manifest_json["seed"] + repeat - 1},
                            ),
                            "repeat": repeat,
                        }
                    )
            counts = {
                v: sum(i["verdict"] == v for i in items)
                for v in ("passed", "failed", "inconclusive", "error")
            }
            return {
                "run_id": str(run_id),
                "catalog": meta,
                "items": items,
                "members": observed,
                "summary": {
                    "counts": counts,
                    "relations": len(items),
                    "families": 1,
                    "verdict": next(
                        (v for v in ("error", "failed", "inconclusive") if counts[v]), "passed"
                    ),
                },
                "comparator": comparator_manifest(),
                "input_hash": canonical_hash(
                    {"manifest": run.manifest_hash, "observations": hashes}
                ),
                "boundary": "只读计算，脚本另存报告；不改历史结论，不调用模型或工具。",
            }
