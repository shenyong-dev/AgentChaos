"""Help is useful offline; current-state navigation stays authorized and read-only."""

import json
import os
import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash, register_version
from app.api.cases import get_case_service
from app.api.guidance import get_guidance_service
from app.cases.service import CaseService
from app.core.config import PROJECT_ROOT, Settings
from app.core.passwords import hash_password
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.executors.service import RunService, case_content
from app.guidance.service import GuidanceService, catalog
from app.main import create_app
from app.models import Artifact, Target, TestCase, TestExecution, TestRun, User
from app.schemas.guidance import GuideAction, GuideContext
from app.schemas.runs import CreateRun


def values(page="library", kind=None, identifier=None, **extra):
    return {
        "page": page,
        "catalog_hash": catalog()[1],
        "object_kind": kind,
        "object_id": str(identifier) if identifier else None,
        **extra,
    }


def test_catalog_covers_actual_routes_and_links_only_to_implemented_navigation():
    guide, _ = catalog()
    actual = set(
        re.findall(
            r"path: '/([a-z]+)(?:/[^']*)?'",
            (PROJECT_ROOT / "frontend/src/main.ts").read_text(encoding="utf-8"),
        )
    )
    assert set(guide["pages"]) == actual
    assert len(guide["terms"]) >= 15
    for page in guide["pages"].values():
        assert all(page[k] for k in ("purpose", "prerequisites", "inputs", "outputs", "empty"))
        assert set(page["actions"]).issubset(guide["actions"])
    for action in guide["actions"].values():
        assert action["path"].lstrip("/") in actual
        assert "command" not in action and "method" not in action


@pytest.mark.parametrize(
    "change",
    [
        {"command": "remove files"},
        {"url": "https://untrusted.invalid"},
        {"catalog_hash": "old"},
        {"object_kind": "case"},
        {"object_id": "1"},
        {"object_kind": "sample", "object_id": "label-this"},
        {"page": "judging", "object_kind": "case", "object_id": "1"},
    ],
)
def test_help_rejects_commands_urls_samples_and_mismatched_context(change):
    with pytest.raises(ValidationError):
        GuideContext.model_validate({**values(), **change})


@pytest.fixture(scope="module")
def lab():
    if os.getenv("AC_INTEGRATION") != "1":
        pytest.skip("Owned real MySQL help checks")
    from test_database import TEST_PASSWORD, disposable_database, migrate
    from test_executor import add_case

    with disposable_database("T17I") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        settings = Settings().model_copy(
            update={
                "mysql_database": engine.url.database,
                "executor_namespace": "ac:t17i:test:" + uuid4().hex,
                "deepseek_api_key": SecretStr(""),
                "allow_paid_model_calls": False,
                "executor_allow_paid_model_calls": False,
                "casegen_allow_paid_model_calls": False,
                "judge_allow_paid_model_calls": False,
            }
        )
        case = add_case(engine)
        draft = add_case(engine, approved=False)
        with Session(engine) as db, db.begin():
            db.add(
                User(
                    username="other_operator",
                    role="operator",
                    enabled=True,
                    password_hash=hash_password(TEST_PASSWORD),
                )
            )
            source = db.get(TestCase, case)
            clones = {}
            for split, knowledge in (("holdout", "base"), ("dev", "legacy")):
                item = {
                    c.name: getattr(source, c.name)
                    for c in TestCase.__table__.columns
                    if c.name not in ("id", "created_at", "updated_at")
                }
                item.update(
                    case_key="HELP-" + uuid4().hex[:16].upper(),
                    family_key="T17I-boundary-control-" + split + "-" + uuid4().hex[:8],
                    dataset_split=split,
                    fixture_json={
                        **source.fixture_json,
                        "dataset": {"knowledge_profile": knowledge},
                    },
                )
                row = TestCase(**item)
                row.content_hash = canonical_hash(case_content(row))
                db.add(row)
                db.flush()
                clones[split + knowledge] = row.id
        version = register_version(engine, RunService(engine, settings).current_manifest())
        run = RunService(engine, settings).create(
            1,
            CreateRun(
                request_id=uuid4(),
                target_version_id=version,
                case_revision_ids=[case],
                budgets={"agent_microusd": 0},
            ),
        )
        with Session(engine) as db:
            execution = db.scalar(select(TestExecution.execution_uuid))
        username, password = "ac_t17i_" + uuid4().hex[:12], uuid4().hex + uuid4().hex
        admin = create_database_engine(Settings(), database="mysql")
        reader = None
        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(
                    text(f"CREATE USER '{username}'@'%' IDENTIFIED BY :password"),
                    {"password": password},
                )
                c.execute(text(f"GRANT SELECT ON `{engine.url.database}`.* TO '{username}'@'%'"))
            read_settings = settings.model_copy(
                update={"mysql_user": username, "mysql_password": SecretStr(password)}
            )
            reader = create_database_engine(read_settings)
            yield {
                "engine": engine,
                "reader": reader,
                "settings": settings,
                "service": GuidanceService(reader, settings),
                "case": case,
                "draft": draft,
                "holdout": clones["holdoutbase"],
                "legacy": clones["devlegacy"],
                "version": version,
                "run": run,
                "execution": execution,
                "password": TEST_PASSWORD,
            }
        finally:
            if reader:
                reader.dispose()
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(text(f"DROP USER IF EXISTS '{username}'@'%'"))
            admin.dispose()


def test_case_help_and_form_handoff_work_with_a_select_only_sql_user(lab):
    service = lab["service"]
    result = service.context(1, GuideContext(**values(kind="case", identifier=lab["case"])))
    assert result["selection"]["status"] == "approved_development"
    action = service.resolve(
        1, GuideAction(**values(kind="case", identifier=lab["case"], action_id="case.prepare"))
    )
    assert action["route"] == {
        "path": "/library",
        "query": {"prepare_case": str(lab["case"])},
        "hash": "",
    }
    assert action["kind"] == "navigate_only"
    assert result["selection"]["references"][0].startswith("case:")


@pytest.mark.parametrize("key", ["draft", "holdout"])
def test_unapproved_and_holdout_cases_never_enter_help_context(lab, key):
    with pytest.raises(AgentError):
        lab["service"].context(1, GuideContext(**values(kind="case", identifier=lab[key])))


def test_nonbase_knowledge_is_explained_but_not_silently_prepared(lab):
    body = values(kind="case", identifier=lab["legacy"])
    assert (
        lab["service"].context(1, GuideContext(**body))["selection"]["knowledge_compatible"]
        is False
    )
    with pytest.raises(AgentError, match="知识条件"):
        lab["service"].resolve(1, GuideAction(**body, action_id="case.prepare"))


def test_owner_role_archival_and_stale_catalog_are_rechecked(lab):
    service = lab["service"]
    with pytest.raises(AgentError):
        service.context(3, GuideContext(**values("runs", "run", lab["run"])))
    with pytest.raises(AgentError):
        service.resolve(2, GuideAction(**values(action_id="page.library")))
    with pytest.raises(AgentError, match="版本已变化"):
        service.context(1, GuideContext(**values(catalog_hash="0" * 64)))
    with Session(lab["engine"]) as db, db.begin():
        db.get(Target, 1).archived_at = datetime.now(UTC)
    try:
        with pytest.raises(AgentError):
            service.context(1, GuideContext(**values("targets", "version", lab["version"])))
    finally:
        with Session(lab["engine"]) as db, db.begin():
            db.get(Target, 1).archived_at = None


def test_waiting_input_state_maps_to_original_controls_without_submitting(lab):
    with Session(lab["engine"]) as db, db.begin():
        run, execution = db.scalar(select(TestRun)), db.scalar(select(TestExecution))
        run.status, execution.status = "running", "waiting_input"
    try:
        body = values("runs", "execution", lab["execution"])
        result = lab["service"].context(1, GuideContext(**body))
        assert result["selection"]["status"] == "waiting_input"
        assert "等待你的输入" in result["selection"]["summary"]
        target = lab["service"].resolve(1, GuideAction(**body, action_id="run.trace"))
        assert target["route"]["path"] == "/runs/" + lab["run"]
        assert target["route"]["hash"] == "#execution-controls"
    finally:
        with Session(lab["engine"]) as db, db.begin():
            db.scalar(select(TestRun)).status = "queued"
            db.scalar(select(TestExecution)).status = "queued"


def test_calibration_help_reads_phase_metadata_without_opening_any_sample_or_label(lab):
    batch = str(uuid4())

    def append(kind, phase):
        with Session(lab["engine"]) as db, db.begin():
            db.add(
                Artifact(
                    owner_id=1,
                    run_id=None,
                    kind=kind,
                    relative_path="forbidden-do-not-read.json",
                    sha256="a" * 64,
                    size_bytes=0,
                    generated_by_revision="free-control-not-human-label",
                    metadata_json={"generation_id": batch, "phase": phase},
                )
            )

    append("judge_request", "request")
    body = GuideContext(**values("judging", "calibration", batch))
    first = lab["service"].context(2, body)
    assert first["selection"]["status"] == "independent_labeling"
    assert "不推荐标签" in first["selection"]["summary"]
    assert "forbidden" not in json.dumps(first)
    with pytest.raises(AgentError):
        lab["service"].context(3, body)
    append("judge_label", "seal")
    assert lab["service"].context(2, body)["selection"]["status"] == "sealed"


def test_unknown_actions_and_missing_selected_objects_are_not_navigation(lab):
    with pytest.raises(AgentError, match="不在"):
        lab["service"].resolve(1, GuideAction(**values(action_id="run.execute")))
    with pytest.raises(AgentError, match="先在当前页面选择"):
        lab["service"].resolve(1, GuideAction(**values(action_id="case.prepare")))


def test_help_http_is_get_only_authenticated_and_no_business_rows_change(lab):
    application = create_app()
    application.dependency_overrides[get_guidance_service] = lambda: lab["service"]
    application.dependency_overrides[get_case_service] = lambda: CaseService(
        lab["reader"], None, lab["settings"]
    )

    def snapshot():
        with lab["engine"].connect() as c:
            return {
                n: c.exec_driver_sql(f"SELECT COUNT(*) FROM {n}").scalar()
                for n in ("ac_test_runs", "ac_test_executions", "sb_bookings", "ac_artifacts")
            }

    before = snapshot()
    with TestClient(application) as client:
        assert client.get("/api/v1/guidance/context", params=values()).status_code == 401
        response = client.get(
            "/api/v1/guidance/context",
            params={k: v for k, v in values().items() if v is not None},
            auth=("demo_operator", lab["password"]),
        )
        assert response.status_code == 200
        invalid = client.get(
            "/api/v1/guidance/actions",
            params={
                **{k: v for k, v in values().items() if v is not None},
                "action_id": "case.prepare",
                "url": "javascript:bad",
            },
            auth=("demo_operator", lab["password"]),
        )
        assert invalid.status_code == 400
        assert client.post("/api/v1/guidance/actions").status_code == 405
    assert snapshot() == before
