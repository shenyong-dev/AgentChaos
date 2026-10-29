"""Discover actual owned runs/versions and approved development cases; no dispatch."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.agents.errors import AgentError
from app.cases.service import CaseService
from app.datasets.service import DatasetService
from app.executors.service import RunService, frozen_case
from app.models import Target, TargetVersion, TestCase, TestExecution, TestRun


class WorkbenchService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def _identity(self, session, owner):
        return CaseService.role(session, owner, "operator")

    def _cases(self, session):
        # Formal calibration/holdout are not a browsing or tuning library.
        dataset = DatasetService(self.engine, self.settings)
        frozen = dataset._read(dataset._row(session, "freeze"))
        ids = [c["id"] for c in frozen["cases"].values()] if frozen else []
        latest = aliased(TestCase)
        return select(TestCase).where(
            TestCase.review_status == "approved",
            TestCase.dataset_split == "dev",
            or_(~TestCase.case_key.startswith("T13-"), TestCase.id.in_(ids)),
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
                        "compatible": v.config_hash == current and t.archived_at is None,
                        "availability": "current_source"
                        if v.config_hash == current
                        else "historical_source_required",
                    }
                    for v, t in rows[:limit]
                ],
                "has_more": len(rows) > limit,
                "next_cursor": rows[limit - 1][0].id if len(rows) > limit else None,
                "current_config_hash": current,
            }

    def cases(self, owner, *, cursor=0, limit=20, query="", category=None):
        with Session(self.engine) as session:
            self._identity(session, owner)
            statement = self._cases(session).where(TestCase.id > cursor)
            if query:
                statement = statement.where(TestCase.case_key.contains(query, autoescape=True))
            if category:
                statement = statement.where(TestCase.category == category)
            rows = list(session.scalars(statement.order_by(TestCase.id).limit(limit + 1)))
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
                        "knowledge_profile": c.fixture_json.get("dataset", {}).get(
                            "knowledge_profile", "base"
                        ),
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
            return frozen_case(row)

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
            return {
                "items": [
                    {
                        "id": r.id,
                        "run_id": r.run_uuid,
                        "status": r.status,
                        "target_version_id": r.target_version_id,
                        "created_at": r.created_at.isoformat(),
                        "executions": executions.get(r.id, 0),
                        "summary": r.summary_json,
                        "event_count": r.event_seq,
                        "manifest_hash": r.manifest_hash,
                        "fault": (r.manifest_json.get("fault_profile") or {})
                        .get("config", {})
                        .get("fault_type"),
                    }
                    for r in rows[:limit]
                ],
                "has_more": len(rows) > limit,
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }
