"""Hash-bound human approval, immutable SQL revisions and cold execution requests."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash, register_version
from app.cases.service import CaseService
from app.core.config import PROJECT_ROOT
from app.datasets.catalog import audit, experiment_plan, load_catalog
from app.executors.control import advisory
from app.executors.service import RunService, case_content, frozen_case, role_configs
from app.faults.service import FaultService
from app.judging.engine import manifest as judge_manifest
from app.models import Artifact, KnowledgeDocument, TestCase
from app.schemas.faults import FaultSpec
from app.schemas.runs import CreateRun

ROOT = PROJECT_ROOT / ".local/datasets"


class DatasetService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def _access(self, session, user, *, review=False):
        account = CaseService.role(session, user, "reviewer" if review else None)
        if account.role != "reviewer" and account.id != 1:
            raise AgentError("dataset_not_found", "找不到当前身份可访问的数据集。", 404)
        return account

    def _row(self, session, phase):
        return session.scalar(
            select(Artifact).where(
                Artifact.kind == "dataset_manifest",
                Artifact.metadata_json["phase"].as_string() == phase,
                Artifact.metadata_json["dataset_id"].as_string() == load_catalog()["dataset_id"],
            )
        )

    def _read(self, row):
        if not row:
            return None
        path = (PROJECT_ROOT / row.relative_path).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise AgentError("dataset_evidence_invalid", "数据集证据路径无效。", 503)
        try:
            raw = path.read_bytes()
            if len(raw) != row.size_bytes or hashlib.sha256(raw).hexdigest() != row.sha256:
                raise ValueError()
            return json.loads(raw)
        except (OSError, ValueError):
            raise AgentError(
                "dataset_evidence_invalid", "数据集证据缺失或哈希不符。", 503
            ) from None

    def _append(self, session, value, phase):
        directory = ROOT / hashlib.sha256(self.engine.url.database.encode()).hexdigest()[:12]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (uuid4().hex + ".json")
        raw = (json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode()
        with path.open("xb") as f:
            f.write(raw)
        session.add(
            Artifact(
                owner_id=1,
                run_id=None,
                kind="dataset_manifest",
                relative_path=path.relative_to(PROJECT_ROOT).as_posix(),
                sha256=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
                generated_by_revision="T13-v1",
                metadata_json={"dataset_id": value["dataset_id"], "phase": phase},
            )
        )

    def summary(self, user):
        value = load_catalog()
        with Session(self.engine) as s:
            self._access(s, user)
            frozen = self._read(self._row(s, "freeze"))
            bound = self._read(self._row(s, "bindings"))
        if frozen and frozen["package_hash"] != value["package_hash"]:
            raise AgentError(
                "dataset_changed_after_freeze", "冻结后文件已变更，拒绝换用新内容。", 409
            )
        return {
            "dataset_id": value["dataset_id"],
            "package_hash": value["package_hash"],
            "audit": audit(value),
            "critical_cases": value["critical_cases"],
            "experiment_plan": experiment_plan(value),
            "frozen": frozen,
            "bindings": bound,
        }

    def case(self, user, key):
        value = load_catalog()
        with Session(self.engine) as s:
            account = self._access(s, user)
        row = next((c for c in value["cases"] if c["id"] == key), None)
        if not row or account.role != "reviewer" and row["dsl"]["dataset_split"] != "dev":
            raise AgentError("dataset_case_not_found", "找不到可访问的用例。", 404)
        return row

    def review(self, reviewer, request):
        value = load_catalog()
        if (
            request.package_hash != value["package_hash"]
            or sorted(request.critical_cases) != value["critical_cases"]
        ):
            raise AgentError(
                "dataset_review_hash_conflict", "审核内容或关键20条与当前清单不符。", 409
            )
        with advisory(self.engine, "dataset:" + value["dataset_id"]):
            with Session(self.engine) as s, s.begin():
                user = self._access(s, reviewer, review=True)
                old = self._read(self._row(s, "freeze"))
                request_hash = canonical_hash(request.model_dump(mode="json"))
                if old:
                    if old["request_hash"] == request_hash and old["reviewer_id"] == reviewer:
                        return old
                    raise AgentError(
                        "dataset_already_frozen", "已有不可变审核，不覆盖或重新批准。", 409
                    )
                now, documents, ids = datetime.now(UTC), {}, {}
                for name, docs in value["knowledge"].items():
                    documents[name] = []
                    for d in docs:
                        existing = s.scalar(
                            select(KnowledgeDocument).where(
                                KnowledgeDocument.document_key == d["document_key"],
                                KnowledgeDocument.revision == d["revision"],
                            )
                        )
                        attrs = {
                            "applicability_json": d["applicability"],
                            **{
                                k: d[k]
                                for k in (
                                    "document_key",
                                    "revision",
                                    "title",
                                    "source_ref",
                                    "source_type",
                                    "content",
                                    "content_hash",
                                )
                            },
                            **{
                                k: datetime.fromisoformat(d[k]) if d[k] else None
                                for k in ("valid_from", "valid_to")
                            },
                        }
                        if existing:
                            if any(getattr(existing, k) != v for k, v in attrs.items()):
                                raise AgentError(
                                    "dataset_knowledge_conflict",
                                    "既有知识修订不同，不能覆盖。",
                                    409,
                                )
                        else:
                            existing = KnowledgeDocument(**attrs)
                            s.add(existing)
                            s.flush()
                        documents[name].append(existing.id)
                for c in value["cases"]:
                    d = c["dsl"]
                    existing = s.scalar(
                        select(TestCase).where(
                            TestCase.case_key == d["case_key"], TestCase.revision == d["revision"]
                        )
                    )
                    if existing:
                        wanted = {**d, "review_status": "approved"}
                        actual = frozen_case(existing)
                        meta = existing.fixture_json.get("dataset", {})
                        if (
                            existing.review_status != "approved"
                            or actual["dsl"] != wanted
                            or meta.get("case_hash") != c["case_hash"]
                            or meta.get("initial_state") != c["initial_state"]
                            or meta.get("independent_expectation") != c["independent_expectation"]
                            or meta.get("knowledge_profile") != c["knowledge_profile"]
                        ):
                            raise AgentError(
                                "dataset_revision_conflict", "既有用例修订不同，不覆盖或借用。", 409
                            )
                        ids[c["id"]] = {
                            "id": existing.id,
                            "content_hash": existing.content_hash,
                            "case_hash": c["case_hash"],
                            "origin_package_hash": meta.get("package_hash"),
                        }
                        continue
                    row = TestCase(
                        case_key=d["case_key"],
                        revision=d["revision"],
                        family_key=d["family_key"],
                        category=d["category"],
                        dataset_split=d["dataset_split"],
                        source="reviewed_catalog:" + value["dataset_id"],
                        turns_json=d["turns"],
                        fixture_json={
                            "fixture": d["fixture"],
                            "reference_clock": d["reference_clock"],
                            "dataset": {
                                "id": value["dataset_id"],
                                "package_hash": value["package_hash"],
                                "case_hash": c["case_hash"],
                                "knowledge_profile": c["knowledge_profile"],
                                "initial_state": c["initial_state"],
                                "independent_expectation": c["independent_expectation"],
                            },
                        },
                        expected_json={
                            "schema_version": "1.1",
                            "assertions": d["assertions"],
                            "task_goal": d["task_goal"],
                        },
                        review_status="approved",
                        reviewer_id=reviewer,
                        reviewed_at=now,
                        content_hash="pending",
                    )
                    row.content_hash = canonical_hash(case_content(row))
                    s.add(row)
                    s.flush()
                    ids[c["id"]] = {
                        "id": row.id,
                        "content_hash": row.content_hash,
                        "case_hash": c["case_hash"],
                    }
                frozen = {
                    "dataset_id": value["dataset_id"],
                    "package_hash": value["package_hash"],
                    "request_hash": request_hash,
                    "request_id": str(request.request_id),
                    "reviewer_id": reviewer,
                    "reviewer_username": user.username,
                    "reviewed_at": now.isoformat(),
                    "note": request.note,
                    "review_origin": "authenticated_human_review_of_assistant_authored_proposal",
                    "reviewer_count_boundary": (
                        "One recorded authenticated reviewer; no invented three-"
                        "person annotation claim"
                    ),
                    "cases": ids,
                    "knowledge_profiles": documents,
                    "plan": experiment_plan(value),
                    "roles": role_configs(self.settings),
                    "judge_manifest": judge_manifest(self.settings),
                    "paid_calls": 0,
                    "budget_authorization": "Planning cap only; no ledger initialized",
                }
                self._append(s, frozen, "freeze")
            return frozen

    def bind(self, user):
        value = load_catalog()
        with advisory(self.engine, "dataset-bind:" + value["dataset_id"]):
            with Session(self.engine) as s:
                self._access(s, user)
                frozen = self._read(self._row(s, "freeze"))
                old = self._read(self._row(s, "bindings"))
            if not frozen or frozen["package_hash"] != value["package_hash"]:
                raise AgentError("dataset_not_frozen", "需先完成真实人工审核。", 409)
            if frozen["roles"] != role_configs(self.settings) or frozen[
                "judge_manifest"
            ] != judge_manifest(self.settings):
                raise AgentError(
                    "dataset_components_changed", "模型或裁判组件已不同于冻结配置。", 409
                )
            manifests = {
                name: RunService(self.engine, self.settings, document_ids=ids).current_manifest()
                for name, ids in frozen["knowledge_profiles"].items()
            }
            if old:
                if any(
                    m["config_hash"] != old["V0"][name]["config_hash"]
                    for name, m in manifests.items()
                ):
                    raise AgentError(
                        "dataset_source_changed", "源码已不同于冻结绑定，不得继续原实验。", 409
                    )
                return old
            current = {
                name: {
                    "target_version_id": register_version(self.engine, m),
                    "config_hash": m["config_hash"],
                }
                for name, m in manifests.items()
            }
            faults = {
                canonical_hash(c["fault"]): FaultService(self.engine).create(
                    FaultSpec.model_validate(c["fault"])
                )["id"]
                for c in value["cases"]
                if c["fault"]
            }
            bound = {
                "dataset_id": value["dataset_id"],
                "package_hash": value["package_hash"],
                "V0": current,
                "V1": None,
                "fault_profiles": faults,
                "source_boundary": "Source-compatible versions, no new Agent outcome validation",
                "bound_at": datetime.now(UTC).isoformat(),
            }
            with Session(self.engine) as s, s.begin():
                self._append(s, bound, "bindings")
            return bound

    def requests(self, user):
        data = self.summary(user)
        value = load_catalog()
        if not data["frozen"] or not data["bindings"]:
            raise AgentError("dataset_not_ready", "审核和冻结版本绑定尚未齐全。", 409)
        # Verify source compatibility without starting a run or a Worker.
        bound = self.bind(user)
        cases = {c["id"]: c for c in value["cases"]}
        requests = []
        with Session(self.engine) as s:
            for job in data["experiment_plan"]["E2"]["episodes"]:
                if job["version"] != "V0":
                    continue
                c = cases[job["case_key"]]
                entry = data["frozen"]["cases"][c["id"]]
                row = s.get(TestCase, entry["id"])
                frozen_case(row)
                if row.content_hash != entry["content_hash"]:
                    raise AgentError("dataset_case_changed", "SQL用例与冻结清单不符。", 409)
                request = CreateRun(
                    request_id=uuid5(
                        UUID("a781cb55-e3f1-41b0-af0d-77e020a460cd"),
                        value["package_hash"] + ":V0:" + c["id"] + str(job["repeat_no"]),
                    ),
                    target_version_id=bound["V0"][c["knowledge_profile"]]["target_version_id"],
                    case_revision_ids=[row.id],
                    repetitions=1,
                    seed=job["seed"],
                    limits=c["limits"],
                    budgets={"agent_microusd": c["agent_cap_microusd"]},
                    fault_profile_id=bound["fault_profiles"][canonical_hash(c["fault"])]
                    if c["fault"]
                    else None,
                )
                requests.append(
                    {
                        "plan_case": c["id"],
                        "plan_repeat_no": job["repeat_no"],
                        "request": request.model_dump(mode="json"),
                        "fault_spec": c["fault"],
                        "activation": (
                            "NOT_DISPATCHED; formal-task authorization and existing "
                            "paid-role ledger required"
                        ),
                    }
                )
        return {
            "dataset_id": value["dataset_id"],
            "package_hash": value["package_hash"],
            "requests": requests,
            "V1": "BLOCKED_UNREGISTERED",
            "paid_calls": 0,
            "boundary": "Validated request drafts only; no test run or execution was created",
        }
