"""T15 real SQL/Redis/HTTP reports lab. Every model is an explicit offline control."""

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.agents.versions import build_manifest, canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evidence.reader import TraceReader
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content
from app.models import Artifact, Booking, Defect, TestCase, TestExecution, TestRun, User
from app.reports.service import ReportService
from app.schemas.reports import ExportReport
from app.schemas.runs import CreateRun

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from agent_test_support import REFERENCE, OfflineGateway  # noqa: E402
from test_database import disposable_database, migrate  # noqa: E402
from test_executor import add_case  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--review-seconds", type=int, default=7200)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--port", type=int, default=18015)
    parser.add_argument("--ui-port", type=int, default=5182)
    args = parser.parse_args()
    if not 1 <= args.review_seconds <= 86400:
        parser.error("Review lifetime must be bounded")
    for port in (args.port, args.ui_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    base = Settings()
    previous = json.loads((PROJECT_ROOT / "docs/evidence/T15/preparation.json").read_bytes())[
        "ledgers"
    ]
    marker = uuid4().hex[:12]
    local = PROJECT_ROOT / ".local/t15" / marker
    evidence = PROJECT_ROOT / "docs/evidence/T15" / ("offline-" + marker)
    local.mkdir(parents=True)
    evidence.mkdir(parents=True)
    stop_file = local / "stop"
    settings = base.model_copy(
        update={
            "allow_paid_model_calls": False,
            "executor_allow_paid_model_calls": False,
            "casegen_allow_paid_model_calls": False,
            "judge_allow_paid_model_calls": False,
            "executor_agent_budget_usd": 0,
            "executor_agent_budget_unlimited": False,
            "executor_namespace": "ac:t15:lab:" + marker,
        }
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)

    def ledgers():
        return {
            key: {field.decode(): int(value) for field, value in redis.hgetall(key).items()}
            for key in previous
        }

    assert ledgers() == previous

    def save(name, value):
        path = evidence / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )

    processes = []
    runtime = None
    sql_user = "ac_t15_" + marker
    sql_password = secrets.token_urlsafe(28)
    user_created = False
    password = base.demo_seed_password.get_secret_value()
    admin = create_database_engine(base, database="mysql")
    with disposable_database("T15") as engine:
        try:
            migrate(engine)
            seed_database(engine, password)
            with Session(engine) as session, session.begin():
                session.add(
                    User(
                        username="t15_other",
                        role="operator",
                        enabled=True,
                        password_hash=session.get(User, 1).password_hash,
                    )
                )
            runtime = AgentRuntime(engine, redis, settings, auto_register=True)
            runs = RunService(engine, settings)
            reports = ReportService(engine, settings)
            gateway = OfflineGateway()
            request_text = gateway.preview()
            case_id = add_case(engine, [{"user": request_text}, {"user": "确认"}], approved=False)
            invalid_case = add_case(
                engine, [{"user": "明确不要预约，只回复说明。"}], approved=False
            )
            with Session(engine) as session, session.begin():
                for identifier, key in (
                    (case_id, "T15-POLICY-CONTROL"),
                    (invalid_case, "T15-INVALID-EXPECTATION"),
                ):
                    case = session.get(TestCase, identifier)
                    case.case_key = key
                    case.family_key = "t15-infrastructure-" + key.lower()
                    case.source = "explicit_offline_control_not_human_reviewed_formal_dataset"
                    case.expected_json = {
                        "schema_version": "1.1",
                        "assertions": [{"type": "booking_count", "equals": 1}],
                    }
                    case.review_status, case.reviewer_id, case.reviewed_at = (
                        "approved",
                        2,
                        REFERENCE,
                    )
                    case.content_hash = canonical_hash(case_content(case))

            def create(identifier=case_id, *, version=None, seed=0):
                run = runs.create(
                    1,
                    CreateRun(
                        request_id=uuid4(),
                        target_version_id=version or runtime.version_id,
                        case_revision_ids=[identifier],
                        seed=seed,
                        budgets={"agent_microusd": 0},
                    ),
                )
                return run, runs.read(1, run)["executions"][0]["execution_id"]

            def fail(identifier=case_id, *, seed=0):
                run, execution = create(identifier, seed=seed)
                model = OfflineGateway()
                model.queue.extend(
                    [
                        AIMessage(
                            content="明确离线故障策略：未执行预约。"
                            + ("报告证据 " * 5000 if identifier == invalid_case else "")
                        )
                        for _ in range(2)
                    ]
                )
                assert (
                    run_execution(
                        engine, redis, settings, execution, gateway_factory=lambda *_: model
                    )["status"]
                    == "failed"
                )
                return run, execution

            failed, failed_execution = fail()
            repeated, _ = fail()
            mismatch, _ = fail(seed=73)
            invalid, invalid_execution = fail(invalid_case)
            first_defects = reports.collect(1, failed)["items"]
            assert (
                reports.collect(1, repeated)["items"][0]["defect_key"]
                == first_defects[0]["defect_key"]
            )
            invalid_defect = reports.collect(1, invalid)["items"][0]

            def control_manifest(*values, **kwargs):
                value = build_manifest(*values, **kwargs)
                value["test_control_variant"] = "T15 offline repair-version fixture; not real V1"
                value["config_hash"] = canonical_hash(
                    {key: item for key, item in value.items() if key != "config_hash"}
                )
                return value

            with (
                patch("app.agents.runtime.build_manifest", control_manifest),
                patch("app.executors.service.build_manifest", control_manifest),
            ):
                repaired_runtime = AgentRuntime(engine, redis, settings, auto_register=True)
                repaired, repaired_execution = create(version=repaired_runtime.version_id)

                class RepairGateway(OfflineGateway):
                    def invoke(self, *values, **kwargs):
                        if not self.queue:
                            with Session(engine) as session:
                                output = session.scalar(
                                    select(TestExecution).where(
                                        TestExecution.execution_uuid == repaired_execution
                                    )
                                ).final_output_json
                            self.confirm(output)
                        return super().invoke(*values, **kwargs)

                model = RepairGateway()
                model.preview()
                assert (
                    run_execution(
                        engine,
                        redis,
                        settings,
                        repaired_execution,
                        gateway_factory=lambda *_: model,
                    )["status"]
                    == "passed"
                )
            cancelled, _ = create()
            runs.cancel(1, cancelled)
            errored, error_execution = create()
            model = OfflineGateway()
            model.queue.append(AgentError("model_offline_control_error", "明确离线错误控制。", 503))
            assert (
                run_execution(
                    engine, redis, settings, error_execution, gateway_factory=lambda *_: model
                )["status"]
                == "error"
            )
            assert (
                not reports.collect(1, cancelled)["items"]
                and not reports.collect(1, errored)["items"]
            )
            assert reports.comparison(1, failed, repaired)["conditions_match"]
            assert not reports.comparison(1, failed, mismatch)["conditions_match"]
            expired = ReportService(
                engine, settings, clock=lambda: datetime.now(UTC) - timedelta(hours=3)
            ).export(1, invalid, ExportReport(request_id=uuid4(), expires_hours=1))
            with Session(engine) as session:
                bookings = list(session.scalars(select(Booking)))
                assert len(bookings) == 1 and bookings[0].status == "confirmed"

            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(
                    text(f"CREATE USER '{sql_user}'@:host IDENTIFIED BY :password"),
                    {"host": "%", "password": sql_password},
                )
                user_created = True
                connection.execute(
                    text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE "
                        f"ON `{engine.url.database}`.* TO '{sql_user}'@:host"
                    ),
                    {"host": "%"},
                )
            env = {
                **os.environ,
                "MYSQL_DATABASE": engine.url.database,
                "MYSQL_USER": sql_user,
                "MYSQL_PASSWORD": sql_password,
                "DEEPSEEK_API_KEY": "",
                "ALLOW_PAID_MODEL_CALLS": "false",
                "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
                "JUDGE_ALLOW_PAID_MODEL_CALLS": "false",
                "EXECUTOR_AGENT_BUDGET_USD": "0",
                "EXECUTOR_AGENT_BUDGET_UNLIMITED": "false",
                "EXECUTOR_NAMESPACE": settings.executor_namespace,
                "AC_API_PROXY_TARGET": f"http://127.0.0.1:{args.port}",
            }

            def spawn(command, cwd, name):
                with (local / (name + ".log")).open("w", encoding="utf-8") as log:
                    process = subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=env,
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    processes.append(process)

            spawn(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.port),
                ],
                PROJECT_ROOT / "backend",
                "api",
            )
            spawn(
                [
                    "node",
                    "node_modules/vite/bin/vite.js",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.ui_port),
                    "--strictPort",
                ],
                PROJECT_ROOT / "frontend",
                "ui",
            )
            deadline = time.monotonic() + 45
            with httpx.Client(timeout=4) as client:
                while True:
                    try:
                        response = client.get(
                            f"http://127.0.0.1:{args.port}/api/v1/reports/runs/{failed}",
                            auth=("demo_operator", password),
                        )
                        ui = client.get(f"http://127.0.0.1:{args.ui_port}/reports")
                        if response.status_code == 200 and ui.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError("Review endpoints did not become ready")
                    time.sleep(0.2)
            review = {
                "api": f"http://127.0.0.1:{args.port}",
                "ui": f"http://127.0.0.1:{args.ui_port}/reports",
                "failed_run": failed,
                "failed_execution": failed_execution,
                "repeated_run": repeated,
                "repaired_run": repaired,
                "repaired_execution": repaired_execution,
                "repair_version": repaired_runtime.version_id,
                "mismatched_run": mismatch,
                "cancelled_run": cancelled,
                "error_run": errored,
                "invalid_expectation_run": invalid,
                "invalid_execution": invalid_execution,
                "invalid_defect": invalid_defect["defect_key"],
                "policy_defect": first_defects[0]["defect_key"],
                "expired_exports": expired["items"],
                "stop_file": str(stop_file),
                "database": engine.url.database,
                "expires_at": (
                    datetime.now(UTC) + timedelta(seconds=args.review_seconds)
                ).isoformat(),
                "processes": [process.pid for process in processes],
                "provider_calls": 0,
                "boundary": (
                    "Explicit offline policy and repair-version fixtures; not real V1, "
                    "provider quality, formal experiment or team human labels. "
                    "SQL/Redis/tools/HTTP are real. Reference clock 2026-10-25."
                ),
            }
            save("review-session.json", review)
            (PROJECT_ROOT / ".local/t15/latest-review.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(review, ensure_ascii=False), flush=True)
            if args.test:
                ui_env = {
                    **env,
                    "AC_E2E_BASE_URL": review["ui"].removesuffix("/reports"),
                    "AC_T15_LAB": json.dumps(review),
                    "AC_T15_PASSWORD": password,
                    "AC_E2E_EVIDENCE_DIR": str(evidence),
                    "PLAYWRIGHT_BROWSERS_PATH": str(PROJECT_ROOT / ".cache/playwright"),
                }
                with (evidence / "ui-tests.txt").open("w", encoding="utf-8") as log:
                    result = subprocess.run(
                        [
                            "node",
                            "node_modules/@playwright/test/cli.js",
                            "test",
                            "tests/reports.spec.ts",
                        ],
                        cwd=PROJECT_ROOT / "frontend",
                        env=ui_env,
                        stdout=log,
                        stderr=log,
                    )
                print(json.dumps({"ui_exit_code": result.returncode}), flush=True)
            if args.review:
                stop_at = time.monotonic() + args.review_seconds
                while time.monotonic() < stop_at and not stop_file.exists():
                    time.sleep(1)
        finally:
            if runtime:
                reader = TraceReader(engine, settings)
                with Session(engine) as session:
                    identifiers = list(session.scalars(select(TestRun.run_uuid)))
                    sessions = list(session.scalars(select(TestExecution.session_id)))
                    artifacts = list(session.scalars(select(Artifact)))
                    save(
                        "sql-bookings.json",
                        [
                            {
                                "scope": row.scope_id,
                                "status": row.status,
                                "room_id": row.room_id,
                                "attendees": row.attendees,
                            }
                            for row in session.scalars(select(Booking))
                        ],
                    )
                    save(
                        "defects.json",
                        [
                            {
                                column.name: getattr(row, column.name)
                                for column in Defect.__table__.columns
                            }
                            for row in session.scalars(select(Defect))
                        ],
                    )
                    save(
                        "artifact-index.json",
                        [
                            {
                                "id": row.id,
                                "kind": row.kind,
                                "sha256": row.sha256,
                                "bytes": row.size_bytes,
                                "metadata": row.metadata_json,
                            }
                            for row in artifacts
                        ],
                    )
                for identifier in identifiers:
                    save(identifier + "-snapshot.json", reader.snapshot(1, identifier))
                    events, cursor = [], None
                    while True:
                        page = reader.page(1, identifier, after=cursor, limit=100)
                        events.extend(page["items"])
                        if not page["has_more"]:
                            break
                        cursor = page["next_cursor"]
                    save(identifier + "-events.json", events)
                for artifact in artifacts:
                    payload = (PROJECT_ROOT / artifact.relative_path).read_bytes()
                    assert hashlib.sha256(payload).hexdigest() == artifact.sha256
                    path = evidence / "artifacts" / str(artifact.id)
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(payload)
            for process in reversed(processes):
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    else:
                        process.terminate()
                    process.wait(timeout=15)
            if user_created:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    connection.execute(
                        text(f"DROP USER IF EXISTS '{sql_user}'@:host"), {"host": "%"}
                    )
            if runtime:
                for identifier in sessions:
                    runtime.saver.delete_thread(identifier)
            keys = list(redis.scan_iter(match=settings.executor_namespace + "*"))
            if keys:
                redis.delete(*keys)
            assert ledgers() == previous
            save(
                "cleanup.json",
                {
                    "owned_processes_stopped": True,
                    "temporary_user_removed": True,
                    "database_removed_on_context_exit": True,
                    "old_ledgers_unchanged": 8,
                    "provider_calls": 0,
                },
            )
    admin.dispose()
    redis.close()


if __name__ == "__main__":
    main()
