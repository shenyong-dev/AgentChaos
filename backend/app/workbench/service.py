"""Discover actual owned runs/versions and approved development cases; no dispatch."""

import hashlib
import json

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.agents.errors import AgentError
from app.cases.service import CaseService
from app.datasets.service import DatasetService
from app.executors.service import RunService, frozen_case
from app.models import Artifact, Target, TargetVersion, TestCase, TestExecution, TestRun
from app.workbench.guidance import case_guide


def reading_summary(row):
    try:
        guide = case_guide(frozen_case(row))
        return {k: guide[k] for k in ("title", "risk", "status", "policy", "guide_hash")}
    except AgentError:
        return {
            "title": "用例内容待核对",
            "risk": "无法验证该修订的内容。",
            "status": "unavailable",
        }


class WorkbenchService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def _identity(self, session, owner):
        return CaseService.role(session, owner, "operator")

    def _cloud_imports(self, session):
        from app.core.cloud import team_cloud
        from app.core.config import PROJECT_ROOT

        if not team_cloud(self.settings):
            return []
        root = (PROJECT_ROOT / ".local/cloud").resolve()
        imports = []
        for record in session.scalars(
            select(Artifact).where(
                Artifact.kind == "dataset_manifest",
                Artifact.metadata_json["phase"].as_string() == "cloud_development_import",
                Artifact.metadata_json["deployment_id"].as_string()
                == self.settings.cloud_deployment_id,
            )
        ):
            path = (PROJECT_ROOT / record.relative_path).resolve()
            try:
                if not path.is_relative_to(root) or not 0 < record.size_bytes <= 65536:
                    continue
                raw = path.read_bytes()
                if (
                    len(raw) != record.size_bytes
                    or hashlib.sha256(raw).hexdigest() != record.sha256
                ):
                    continue
                value = json.loads(raw)
                if (
                    value["deployment_id"] != self.settings.cloud_deployment_id
                    or value["kind"] != "imported_approved_development_revisions"
                ):
                    continue
                entries = value["cases"]
                if not isinstance(entries, list) or len(entries) > 120:
                    continue
                for entry in entries:
                    identifier, digest = entry["id"], entry["content_hash"]
                    if (
                        type(identifier) is int
                        and identifier > 0
                        and isinstance(digest, str)
                        and len(digest) == 64
                        and all(c in "0123456789abcdef" for c in digest)
                    ):
                        imports.append(
                            (TestCase.id == identifier) & (TestCase.content_hash == digest)
                        )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return imports

    def _cases(self, session):
        # Formal calibration/holdout are not a browsing or tuning library.
        dataset = DatasetService(self.engine, self.settings)
        frozen = dataset._read(dataset._row(session, "freeze"))
        ids = [c["id"] for c in frozen["cases"].values()] if frozen else []
        latest = aliased(TestCase)
        return select(TestCase).where(
            TestCase.review_status == "approved",
            TestCase.dataset_split == "dev",
            TestCase.source != "reduction_trial:T17M",
            or_(
                ~TestCase.case_key.startswith("T13-"),
                TestCase.id.in_(ids),
                *self._cloud_imports(session),
            ),
            ~select(latest.id)
            .where(
                latest.case_key == TestCase.case_key,
                latest.review_status == "approved",
                latest.revision > TestCase.revision,
            )
            .exists(),
        )

    def overview(self, owner):
        with Session(self.engine) as session:
            user = self._identity(session, owner)
            counts = dict(
                session.execute(
                    select(TestRun.status, func.count())
                    .where(TestRun.created_by == owner)
                    .group_by(TestRun.status)
                ).all()
            )
            approved = session.scalar(
                select(func.count()).select_from(self._cases(session).subquery())
            )
            targets = session.scalar(
                select(func.count()).select_from(Target).where(Target.owner_id == owner)
            )
            modes = list(
                session.scalars(
                    select(TestExecution.timing_json["gateway_mode"].as_string())
                    .join(TestRun)
                    .where(TestRun.created_by == owner)
                    .distinct()
                )
            )
        return {
            "identity": {"username": user.username, "role": user.role},
            "counts": {
                "runs": sum(counts.values()),
                "run_statuses": counts,
                "development_cases": approved,
                "targets": targets,
            },
            "executor": {
                "observed_gateway_modes": sorted(m for m in modes if m),
                "paid_calls_enabled": self.settings.executor_allow_paid_model_calls,
                "worker_liveness": "not_probed",
                "concurrency": self.settings.run_concurrency,
                "boundary": "Queue acceptance does not prove a live Worker or model execution.",
            },
            "versions": self.versions(owner, limit=50),
        }

    def versions(self, owner, *, cursor=0, limit=20):
        current = RunService(self.engine, self.settings).current_manifest()["config_hash"]
        from app.adapters.refund import RefundAdapter

        refund_current = RefundAdapter(self.engine, self.settings).manifest()["config_hash"]
        with Session(self.engine) as session:
            self._identity(session, owner)
            query = (
                select(TargetVersion, Target)
                .join(Target)
                .where(Target.owner_id == owner)
                .order_by(TargetVersion.id.desc())
            )
            if cursor:
                query = query.where(TargetVersion.id < cursor)
            rows = session.execute(query.limit(limit + 1)).all()
            return {
                "items": [
                    {
                        "id": v.id,
                        "version_no": v.version_no,
                        "target": t.name,
                        "target_id": t.id,
                        "adapter_type": t.adapter_type,
                        "config_hash": v.config_hash,
                        "prompt_hash": v.prompt_hash,
                        "graph_revision": v.graph_revision,
                        "adapter_revision": v.adapter_revision,
                        "model": v.model_config_json.get("model_requested"),
                        "capabilities": t.capabilities_json,
                        "compatible": v.config_hash
                        == (refund_current if t.adapter_type == "reference_refund" else current)
                        and t.archived_at is None,
                        "availability": "current_source"
                        if v.config_hash
                        == (refund_current if t.adapter_type == "reference_refund" else current)
                        else "historical_source_required",
                    }
                    for v, t in rows[:limit]
                ],
                "has_more": len(rows) > limit,
                "next_cursor": rows[limit - 1][0].id if len(rows) > limit else None,
                "current_config_hash": current,
            }

    def cases(self, owner, *, cursor=0, limit=20, query="", category=None, order="oldest"):
        with Session(self.engine) as session:
            self._identity(session, owner)
            statement = self._cases(session)
            if cursor:
                statement = statement.where(
                    TestCase.id < cursor if order == "newest" else TestCase.id > cursor
                )
            if query:
                statement = statement.where(TestCase.case_key.contains(query, autoescape=True))
            if category:
                statement = statement.where(TestCase.category == category)
            ordering = TestCase.id.desc() if order == "newest" else TestCase.id.asc()
            rows = list(session.scalars(statement.order_by(ordering).limit(limit + 1)))
            return {
                "items": [
                    {
                        "id": c.id,
                        "case_key": c.case_key,
                        "revision": c.revision,
                        "family_key": c.family_key,
                        "category": c.category,
                        "split": c.dataset_split,
                        "content_hash": c.content_hash,
                        "turns": len(c.turns_json),
                        "review_status": c.review_status,
                        "guidance": reading_summary(c),
                        "knowledge_profile": "not_applicable"
                        if c.fixture_json.get("fixture") == "refund-reference-v1"
                        else c.fixture_json.get("dataset", {}).get("knowledge_profile", "base"),
                        "adapter_type": "reference_refund"
                        if c.fixture_json.get("fixture") == "refund-reference-v1"
                        else "langgraph_booking",
                        "default_knowledge_compatible": c.fixture_json.get("dataset", {}).get(
                            "knowledge_profile", "base"
                        )
                        == "base",
                    }
                    for c in rows[:limit]
                ],
                "has_more": len(rows) > limit,
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
                "boundary": "Approved development revisions only; no formal holdout content.",
            }

    def case(self, owner, identifier):
        with Session(self.engine) as session:
            self._identity(session, owner)
            row = session.scalar(self._cases(session).where(TestCase.id == identifier))
            if row is None:
                raise AgentError("case_not_found", "找不到可浏览的已审核开发用例。", 404)
            snapshot = frozen_case(row)
            return {**snapshot, "guidance": case_guide(snapshot)}

    def runs(self, owner, *, cursor=0, limit=20, status=None, query=""):
        with Session(self.engine) as session:
            self._identity(session, owner)
            statement = select(TestRun).where(TestRun.created_by == owner)
            if cursor:
                statement = statement.where(TestRun.id < cursor)
            if status:
                statement = statement.where(TestRun.status == status)
            if query:
                statement = statement.where(TestRun.run_uuid.startswith(query, autoescape=True))
            rows = list(session.scalars(statement.order_by(TestRun.id.desc()).limit(limit + 1)))
            ids = [r.id for r in rows[:limit]]
            executions = (
                dict(
                    session.execute(
                        select(TestExecution.run_id, func.count())
                        .where(TestExecution.run_id.in_(ids))
                        .group_by(TestExecution.run_id)
                    ).all()
                )
                if ids
                else {}
            )
            modes, execution_counts = {}, {}
            if ids:
                for run_id, timing, status in session.execute(
                    select(
                        TestExecution.run_id, TestExecution.timing_json, TestExecution.status
                    ).where(TestExecution.run_id.in_(ids))
                ):
                    counts = execution_counts.setdefault(run_id, {})
                    counts[status] = counts.get(status, 0) + 1
                    value = (timing or {}).get("gateway_mode")
                    if isinstance(value, str):
                        modes.setdefault(run_id, set()).add(value)
            return {
                "items": [
                    {
                        "id": r.id,
                        "run_id": r.run_uuid,
                        "status": r.status,
                        "target_version_id": r.target_version_id,
                        "created_at": r.created_at.isoformat(),
                        "executions": executions.get(r.id, 0),
                        "execution_counts": execution_counts.get(r.id, {}),
                        "summary": r.summary_json,
                        "event_count": r.event_seq,
                        "manifest_hash": r.manifest_hash,
                        "observed_modes": sorted(modes.get(r.id, set())),
                        "declared_gateway_mode": (r.manifest_json.get("workflow") or {}).get(
                            "declared_gateway_mode"
                        ),
                        "fault": (r.manifest_json.get("fault_profile") or {})
                        .get("config", {})
                        .get("fault_type"),
                    }
                    for r in rows[:limit]
                ],
                "has_more": len(rows) > limit,
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }
