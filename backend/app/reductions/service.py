"""Owner-bound append-only reduction plans; paid work is always an explicit action."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.executors.control import advisory
from app.executors.service import RunService, case_content
from app.models import Artifact, TestCase, TestExecution, TestRun
from app.reductions.engine import (
    REPEATS,
    TRIAL_SOURCE,
    conditions,
    input_size,
    proposals,
    repeat_summary,
    signature,
    subset,
    usage_metrics,
)
from app.reports.analysis import candidate_fingerprint
from app.reports.service import ReportService
from app.schemas.runs import CreateRun

ROOT = PROJECT_ROOT / ".local/reductions"


class ReductionService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings
        self.runs = RunService(engine, settings)
        self.reports = ReportService(engine, settings)

    def _load(self, session, owner, identifier):
        rows = list(
            session.scalars(
                select(Artifact)
                .where(
                    Artifact.kind == "reduction_revision",
                    Artifact.metadata_json["plan_id"].as_string() == str(identifier),
                )
                .order_by(Artifact.id)
            )
        )
        if not rows:
            raise AgentError("reduction_not_found", "找不到当前账号的提炼计划。", 404)
        run = session.get(TestRun, rows[0].run_id)
        RunService.owned_run(session, owner, run.run_uuid)
        row = rows[-1]
        path = (PROJECT_ROOT / row.relative_path).resolve()
        try:
            if not path.is_relative_to(ROOT.resolve()):
                raise ValueError()
            payload = path.read_bytes()
            if len(payload) != row.size_bytes or hashlib.sha256(payload).hexdigest() != row.sha256:
                raise ValueError()
            value = json.loads(payload)
            if value["plan_id"] != str(identifier) or value["owner"] != owner:
                raise ValueError()
        except (OSError, ValueError, KeyError):
            raise AgentError(
                "reduction_evidence_invalid", "提炼证据缺失或哈希不匹配。", 503
            ) from None
        return value

    def _save(self, session, value, *, private_case_id=None):
        namespace = hashlib.sha256(self.engine.url.database.encode()).hexdigest()[:12]
        directory = ROOT / namespace / value["plan_id"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (uuid4().hex + ".json")
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        if len(payload) > 2_000_000:
            raise AgentError("reduction_size_limit", "提炼记录超过上限。", 409)
        with path.open("xb") as handle:
            handle.write(payload)
        session.add(
            Artifact(
                run_id=value["origin_run_database_id"],
                owner_id=None,
                kind="reduction_revision",
                relative_path=path.relative_to(PROJECT_ROOT).as_posix(),
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                generated_by_revision="T17M-v1",
                metadata_json={
                    "plan_id": value["plan_id"],
                    "defect_key": value["defect_key"],
                    "revision_hash": canonical_hash(value),
                    "private_case_id": private_case_id,
                },
            )
        )

    @staticmethod
    def _action(value, request, operation):
        key = str(request.request_id)
        digest = canonical_hash({"body": request.model_dump(mode="json"), "operation": operation})
        previous = value["actions"].get(key)
        if previous:
            if previous != digest:
                raise AgentError("reduction_request_conflict", "同一请求标识已用于其他内容。", 409)
            return False
        if request.expected_hash != canonical_hash(value):
            raise AgentError("reduction_revision_conflict", "提炼计划已变化，请刷新后核对。", 409)
        value["actions"][key] = digest
        return True

    def latest(self, owner, defect):
        with Session(self.engine) as session:
            row, _ = self.reports._owned_defect(session, owner, defect)
            record = session.scalar(
                select(Artifact)
                .where(
                    Artifact.kind == "reduction_revision",
                    Artifact.metadata_json["defect_key"].as_string() == row.defect_key,
                )
                .order_by(Artifact.id.desc())
            )
            identifier = record.metadata_json["plan_id"] if record else None
        return self.read(owner, identifier) if identifier else None

    def create(self, owner, defect, request, *, baseline_runs=()):
        # Trusted benchmark-only import of three predeclared executions. Not an HTTP input.
        if baseline_runs and (len(baseline_runs) != REPEATS or len(set(baseline_runs)) != REPEATS):
            raise AgentError(
                "reduction_baseline_invalid", "基线必须是三个不同的预先计划运行。", 400
            )
        identifier = str(request.request_id)
        with advisory(self.engine, "reduction:" + identifier) as connection:
            with Session(connection) as session, session.begin():
                prior = session.scalar(
                    select(Artifact.id).where(
                        Artifact.kind == "reduction_revision",
                        Artifact.metadata_json["plan_id"].as_string() == identifier,
                    )
                )
                if prior:
                    value = self._load(session, owner, identifier)
                    if value["defect_key"] != defect:
                        raise AgentError(
                            "reduction_request_conflict", "请求标识已用于另一缺陷。", 409
                        )
                else:
                    row, execution = self.reports._owned_defect(session, owner, defect)
                    run = session.get(TestRun, execution.run_id)
                    report = self.reports.read(owner, run.run_uuid)
                    original = next(
                        e
                        for e in report["executions"]
                        if e["execution_id"] == execution.execution_uuid
                    )
                    original["seed"] = execution.seed
                    found = signature(original)
                    content = original["case"]["content"]
                    if row.category != "rule_violation" or not found or len(content["turns"]) < 2:
                        raise AgentError(
                            "reduction_unsupported",
                            "需要至少两轮、完整SQL证据的预约数量规则失败。",
                            409,
                        )
                    assertion = next(
                        a for a in original["assertions"] if a["id"] == found["assertion_id"]
                    )
                    if candidate_fingerprint(report, original, assertion) != row.fingerprint:
                        raise AgentError(
                            "reduction_defect_mismatch", "该数量失败不是当前缺陷的客观判据。", 409
                        )
                    if content["dataset_split"] != "dev" or run.manifest_json.get("metamorphic"):
                        raise AgentError(
                            "reduction_dev_only",
                            "仅支持普通开发用例，不缩减冻结保留集或关系组。",
                            403,
                        )
                    if not self.runs.configuration_matches(run.manifest_json):
                        raise AgentError(
                            "reduction_version_unavailable",
                            "当前Worker不能复验该历史源码；请保留原记录，先在当前版本重现。",
                            409,
                        )
                    choices = proposals(content["turns"])
                    value = {
                        "schema": "reduction-v1",
                        "plan_id": identifier,
                        "owner": owner,
                        "defect_key": defect,
                        "origin_run_database_id": run.id,
                        "origin_run": run.run_uuid,
                        "origin_execution": execution.execution_uuid,
                        "origin_case": content,
                        "signature": found,
                        "conditions": conditions(report, original),
                        "actions": {},
                        "created_at": datetime.now(UTC).isoformat(),
                        "variants": [
                            {
                                "keep": list(range(len(content["turns"]))),
                                "review": None,
                                "runs": [],
                                "case_id": None,
                            }
                        ]
                        + [
                            {"keep": p["keep"], "review": None, "runs": [], "case_id": None}
                            for p in choices
                        ],
                        "draft": None,
                    }
                    if baseline_runs:
                        if run.run_uuid not in baseline_runs:
                            raise AgentError(
                                "reduction_baseline_invalid", "原失败须属于声明的基线。", 409
                            )
                        for run_id in baseline_runs:
                            RunService.owned_run(session, owner, run_id)
                        value["variants"][0]["runs"] = list(baseline_runs)
                    self._save(session, value)
        return self.read(owner, identifier)

    def review(self, owner, identifier, index, request):
        with advisory(self.engine, "reduction:" + str(identifier)) as connection:
            with Session(connection) as session, session.begin():
                value = self._load(session, owner, identifier)
                if self._action(value, request, f"review:{index}"):
                    if index < 1 or index >= len(value["variants"]):
                        raise AgentError("reduction_candidate_not_found", "找不到缩减候选。", 404)
                    variant = value["variants"][index]
                    if (
                        variant["review"]
                        or variant["runs"]
                        or not request.human_attestation
                        or not request.note.strip()
                    ):
                        raise AgentError(
                            "reduction_review_locked",
                            "请明确人工核对；已封存的语义决定不能覆盖。",
                            409,
                        )
                    subset(value["origin_case"]["turns"], request.keep)
                    variant["keep"] = request.keep
                    variant["review"] = {"by": owner, **request.model_dump(mode="json")}
                    self._save(session, value)
        return self.read(owner, identifier)

    def execute(self, owner, identifier, index, request):
        with advisory(self.engine, "reduction:" + str(identifier)) as connection:
            with Session(connection) as session, session.begin():
                value = self._load(session, owner, identifier)
                fresh = self._action(value, request, f"execute:{index}")
                if index < 0 or index >= len(value["variants"]):
                    raise AgentError("reduction_candidate_not_found", "找不到缩减候选。", 404)
                variant = value["variants"][index]
                if not request.paid_attestation:
                    raise AgentError(
                        "reduction_paid_confirmation_required", "请确认三次执行的付费后果。", 403
                    )
                if index and not (variant["review"] or {}).get("semantic_preserved"):
                    raise AgentError(
                        "reduction_semantics_required", "缩减候选须先经人工确认保留原意。", 409
                    )
                if fresh and variant["runs"]:
                    existing = set(
                        session.scalars(
                            select(TestRun.run_uuid).where(
                                TestRun.run_uuid.in_(variant["runs"]), TestRun.created_by == owner
                            )
                        )
                    )
                    if len(existing) == REPEATS:
                        raise AgentError(
                            "reduction_already_started",
                            "已创建三次验证，请刷新原运行，不能追加重复。",
                            409,
                        )
                    self._save(session, value)
                frozen = value["conditions"]
                if (
                    frozen["gateway_mode"] != "real_provider"
                    and self.settings.executor_allow_paid_model_calls
                ):
                    raise AgentError(
                        "reduction_gateway_mismatch",
                        "离线控制只能在离线验证环境复验，不能转成真实付费运行。",
                        409,
                    )
                if frozen["limits"]["concurrency"] > self.settings.run_concurrency:
                    raise AgentError(
                        "reduction_conditions_changed",
                        "并发限制已变化，不能混合不同条件复验。",
                        409,
                    )
                if not self.runs.configuration_matches(frozen) or (
                    frozen["budget"]["authorization_id"]
                    != self.settings.executor_budget_authorization_id
                    or frozen["budget"]["authorization_unlimited"]
                    != self.settings.executor_agent_budget_unlimited
                ):
                    raise AgentError(
                        "reduction_conditions_changed",
                        "源码、角色或授权已变化，不能按旧条件付费复验。",
                        409,
                    )
                if not variant["runs"]:
                    content = value["origin_case"]
                    case = TestCase(
                        case_key="RM-" + uuid4().hex.upper(),
                        revision=1,
                        family_key=content["family_key"],
                        category=content["category"],
                        dataset_split="dev",
                        source=TRIAL_SOURCE,
                        turns_json=[content["turns"][i] for i in variant["keep"]],
                        fixture_json=content["fixture"],
                        expected_json=content["expected"],
                        review_status="draft",
                        reviewer_id=None,
                        reviewed_at=None,
                        content_hash="0" * 64,
                    )
                    case.content_hash = canonical_hash(case_content(case))
                    session.add(case)
                    session.flush()
                    variant["case_id"] = case.id
                    variant["runs"] = [
                        str(uuid5(UUID(str(identifier)), f"{index}:{repeat}"))
                        for repeat in range(REPEATS)
                    ]
                    self._save(session, value, private_case_id=case.id)
            # Journal committed first; a lost response resumes these exact IDs, never a new batch.
            for run_id in variant["runs"]:
                frozen = value["conditions"]
                budgets = {
                    k: frozen["budget"][k]
                    for k in ("agent_microusd", "case_generator_microusd", "judge_microusd")
                }
                values = CreateRun(
                    request_id=run_id,
                    target_version_id=frozen["target_version_id"],
                    case_revision_ids=[variant["case_id"]],
                    repetitions=1,
                    seed=frozen["seed"],
                    limits=frozen["limits"],
                    budgets=budgets,
                    fault_profile_id=(frozen["fault_profile"] or {}).get("id"),
                )
                self.runs.create(
                    owner,
                    values,
                    workflow={
                        "reduction_id": str(identifier),
                        "reduction_case_ids": [variant["case_id"]],
                    },
                )
        return self.read(owner, identifier)

    def read(self, owner, identifier):
        with Session(self.engine) as session:
            value = self._load(session, owner, identifier)
        result = deepcopy(value)
        result["revision_hash"] = canonical_hash(value)
        for variant in result["variants"]:
            observations, links = [], []
            turns = [value["origin_case"]["turns"][i] for i in variant["keep"]]
            variant["turns"], variant["size"] = turns, input_size(turns)
            for run_id in variant["runs"]:
                observed = {"execution_id": None, "status": "unknown"}
                try:
                    run = self.runs.read(owner, run_id)
                    observed["status"] = run["status"]
                    if run["status"] in {"completed", "failed", "cancelled"}:
                        report = self.reports.read(owner, run_id)
                        observed = report["executions"][0]
                        with Session(self.engine) as session:
                            observed["seed"] = session.scalar(
                                select(TestExecution.seed).where(
                                    TestExecution.execution_uuid == observed["execution_id"]
                                )
                            )
                        if (
                            conditions(report, observed) != value["conditions"]
                            or observed["case"]["content"]["turns"] != turns
                        ):
                            observed = {
                                "execution_id": observed["execution_id"],
                                "status": "condition_mismatch",
                                "usage": observed.get("usage"),
                            }
                except AgentError as exc:
                    observed["read_error"] = exc.code
                observations.append(observed)
                links.append(
                    {
                        "run_id": run_id,
                        "status": observed["status"],
                        "usage": observed.get("usage"),
                        "read_error": observed.get("read_error"),
                    }
                )
            variant["summary"] = repeat_summary(value["signature"], observations)
            variant["usage_metrics"] = usage_metrics(observations)
            variant["executions"] = links
        baseline = result["variants"][0]["summary"]["verified"]
        for index, variant in enumerate(result["variants"]):
            variant["verified"] = (
                baseline
                and variant["summary"]["verified"]
                and (index == 0 or bool((variant["review"] or {}).get("semantic_preserved")))
            )
        return result

    def stage(self, owner, identifier, index, request):
        from app.cases import evidence
        from app.cases.service import CaseService
        from app.schemas.reductions import VerifiedReductionCandidate

        with advisory(self.engine, "reduction:" + str(identifier)) as connection:
            checked = self.read(owner, identifier)
            with Session(connection) as session, session.begin():
                value = self._load(session, owner, identifier)
                fresh = self._action(value, request, f"stage:{index}")
                if fresh:
                    if (
                        index < 1
                        or index >= len(checked["variants"])
                        or not checked["variants"][index]["verified"]
                    ):
                        raise AgentError(
                            "reduction_not_verified",
                            "须原例与候选各三次同签名复现，才能提交审核。",
                            409,
                        )
                    if value["draft"]:
                        raise AgentError(
                            "reduction_draft_exists", "本计划已有回归草稿，请继续原审核。", 409
                        )
                    origin = value["origin_case"]
                    candidate = VerifiedReductionCandidate(
                        profile="normal",
                        title="已验证缩减 · " + origin["case_key"][:65],
                        rationale="保留原预期的删除轮次缩减；原例及候选各三次同签名复现。仍需人工审核。",
                        turns=checked["variants"][index]["turns"],
                        assertions=origin["expected"]["assertions"],
                        task_goal=origin["expected"].get("task_goal"),
                    )
                    raw = candidate.model_dump(mode="json")
                    validation = reduction_validation(raw, origin["fixture"]["reference_clock"])
                    batch = str(uuid5(UUID(str(identifier)), "regression-draft"))
                    frozen = {
                        "mode": "verified_reduction",
                        "reference_clock": origin["fixture"]["reference_clock"],
                        "risks": {"normal": {}},
                        "family_key": origin["family_key"],
                        "category": origin["category"],
                        "reduction": {
                            "plan_id": str(identifier),
                            "variant": index,
                            "owner": owner,
                            "defect_key": value["defect_key"],
                            "proof_hash": canonical_hash(checked["variants"][index]["summary"]),
                            "candidate_hash": canonical_hash(raw),
                        },
                    }
                    evidence.append(
                        session,
                        self.settings,
                        owner,
                        batch,
                        "casegen_request",
                        frozen,
                        {"mode": "verified_reduction"},
                    )
                    artifact = evidence.append(
                        session,
                        self.settings,
                        owner,
                        batch,
                        "casegen_result",
                        {
                            "raw": raw,
                            "candidate_hash": canonical_hash(raw),
                            "schema_errors": [],
                            "validation": validation,
                            "duplicates": [],
                            "status": "valid",
                            "index": 0,
                            "mode": "verified_reduction",
                        },
                        {"phase": "candidate", "status": "valid", "index": 0},
                    )
                    session.add(
                        CaseService._new_case(
                            candidate,
                            frozen,
                            owner,
                            batch,
                            artifact.id,
                            validation,
                            case_key="CG-" + uuid4().hex.upper(),
                            revision=1,
                        )
                    )
                    evidence.append(
                        session,
                        self.settings,
                        owner,
                        batch,
                        "casegen_result",
                        {
                            "candidate_ids": [artifact.id],
                            "attempts": [],
                            "error": None,
                            "ledger": {},
                            "requested": 1,
                        },
                        {"phase": "batch_result"},
                    )
                    value["draft"] = {
                        "batch_id": batch,
                        "candidate_id": artifact.id,
                        "variant": index,
                    }
                    self._save(session, value)
        return self.read(owner, identifier)

    def validate_draft(self, frozen, raw):
        proof = frozen["reduction"]
        checked = self.read(proof["owner"], proof["plan_id"])
        index = proof["variant"]
        if (
            not checked["variants"][index]["verified"]
            or not checked["draft"]
            or checked["draft"]["variant"] != index
            or canonical_hash(checked["variants"][index]["summary"]) != proof["proof_hash"]
            or canonical_hash(raw) != proof["candidate_hash"]
            or raw["turns"] != checked["variants"][index]["turns"]
        ):
            raise AgentError(
                "reduction_proof_invalid", "缩减复验依据不完整或草稿已修改，须重新验证。", 409
            )
        return reduction_validation(raw, frozen["reference_clock"])


def reduction_validation(raw, reference):
    from app.cases.validation import text_fingerprint

    return {
        "issues": [],
        "text_fingerprint": text_fingerprint(raw["turns"], reference),
        "semantic_fingerprint": canonical_hash(
            {
                "turns": raw["turns"],
                "assertions": raw["assertions"],
                "task_goal": raw.get("task_goal"),
                "reference": reference,
            }
        ),
        "source": "verified_reduction",
    }
