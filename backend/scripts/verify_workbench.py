"""T14 real HTTP/SQL/Redis/UI lab with a declared offline model and no provider calls."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash, register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evidence.reader import TraceReader
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content
from app.faults.service import FaultService
from app.models import Artifact, Booking, TestCase, TestExecution, TestRun, User
from app.schemas.faults import FaultSpec
from app.schemas.runs import CreateRun

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from agent_test_support import REFERENCE, OfflineGateway, tool  # noqa: E402
from test_database import disposable_database, migrate  # noqa: E402


class SlowOfflineGateway(OfflineGateway):
    def invoke(self, *args, **kwargs):
        time.sleep(0.18)  # Explicit offline fixture latency, not simulated UI events.
        return super().invoke(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--review-seconds", type=int, default=7200)
    parser.add_argument("--port", type=int, default=18014)
    parser.add_argument("--ui-port", type=int, default=5181)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.review_seconds <= 86400:
        parser.error("Use a bounded review lifetime")
    for port in (args.port, args.ui_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    base = Settings()
    marker = uuid4().hex[:12]
    local = PROJECT_ROOT / ".local/t14" / marker
    evidence = PROJECT_ROOT / "docs/evidence/T14" / ("offline-" + marker)
    local.mkdir(parents=True)
    evidence.mkdir(parents=True)
    stop_file = local / "stop"
    settings = base.model_copy(
        update={
            "allow_paid_model_calls": False,
            "executor_allow_paid_model_calls": False,
            "casegen_allow_paid_model_calls": False,
            "judge_allow_paid_model_calls": False,
            "executor_agent_budget_unlimited": False,
            "executor_agent_budget_usd": 0,
            "executor_namespace": "ac:t14:lab:" + marker,
        }
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    previous = json.loads((PROJECT_ROOT / "docs/evidence/T14/preparation.json").read_bytes())[
        "ledgers"
    ]

    def ledgers():
        return {k: {f.decode(): int(v) for f, v in redis.hgetall(k).items()} for k in previous}

    assert ledgers() == previous

    def save(name, value):
        (evidence / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )

    processes = []
    worker_stop = threading.Event()
    worker = None
    runtime = None
    sql_user = "ac_t14_" + marker
    sql_password = secrets.token_urlsafe(28)
    user_created = False
    password = base.demo_seed_password.get_secret_value()
    admin = create_database_engine(base, database="mysql")
    with disposable_database("T14") as engine:
        try:
            migrate(engine)
            seed_database(engine, password)
            runtime = AgentRuntime(
                engine,
                redis,
                settings,
                prefix=settings.executor_namespace + ":session",
                auto_register=True,
            )
            historical = {
                **runtime.manifest,
                "validation_boundary": (
                    "T14 synthetic historical-source UI control; no Agent outcome claim"
                ),
            }
            historical["config_hash"] = canonical_hash(
                {k: v for k, v in historical.items() if k != "config_hash"}
            )
            register_version(engine, historical)
            ids = {}
            with Session(engine) as session, session.begin():
                for index in range(26):
                    name = (
                        "T14-CONFIRM"
                        if index == 0
                        else "T14-WRONG-EXPECTATION"
                        if index == 1
                        else f"T14-READ-{index:02}"
                    )
                    if index == 25:
                        name = "T14-NONBASE"
                    gateway = OfflineGateway()
                    content = (
                        gateway.preview()
                        if index == 0
                        else "明确离线基础设施样例：只回复说明，不预约。"
                    )
                    row = TestCase(
                        case_key=name,
                        revision=1,
                        family_key="t14-mechanical-" + str(index),
                        category="normal",
                        dataset_split="dev",
                        source="explicit_offline_infrastructure_control_not_human_dataset_review",
                        turns_json=[{"user": content}],
                        fixture_json={
                            "fixture": "booking-default-v1",
                            "reference_clock": REFERENCE.isoformat(),
                            **({"dataset": {"knowledge_profile": "legacy"}} if index == 25 else {}),
                        },
                        expected_json={
                            "schema_version": "1.1",
                            "assertions": [
                                {"type": "booking_count", "equals": 1 if index in (0, 1) else 0}
                            ],
                        },
                        review_status="approved",
                        reviewer_id=2,
                        reviewed_at=REFERENCE,
                        content_hash="pending",
                    )
                    row.content_hash = canonical_hash(case_content(row))
                    session.add(row)
                    session.flush()
                    ids[name] = row.id
                owner = session.get(User, 1)
                session.add(
                    User(username="t14_other", password_hash=owner.password_hash, role="operator")
                )
            fault = FaultService(engine).create(
                FaultSpec(
                    profile_key="T14-READ-CORRUPTION",
                    fault_type="F03",
                    trigger_stage="POST_RESULT",
                    target_tool="query_availability",
                    seed=13029,
                )
            )
            service = RunService(engine, settings)
            failed = service.create(
                1,
                CreateRun(
                    request_id=uuid4(),
                    target_version_id=runtime.version_id,
                    case_revision_ids=[ids["T14-WRONG-EXPECTATION"]],
                    budgets={"agent_microusd": 0},
                ),
            )
            gateways = {}
            errors = []

            def work():
                while not worker_stop.is_set():
                    try:
                        service.expire_deadlines()
                        for identifier in service.candidates():
                            with Session(engine) as session:
                                execution = session.scalar(
                                    select(TestExecution).where(
                                        TestExecution.execution_uuid == identifier
                                    )
                                )
                                case_key = execution.input_snapshot_json["content"]["case_key"]
                                output = execution.final_output_json
                                fault_enabled = bool(
                                    session.get(TestRun, execution.run_id).manifest_json.get(
                                        "fault_profile"
                                    )
                                )
                            gateway = gateways.get(identifier)
                            if gateway is None:
                                gateway = SlowOfflineGateway()
                                gateways[identifier] = gateway
                                if case_key == "T14-CONFIRM":
                                    gateway.preview()
                                    if fault_enabled:
                                        # This declared offline script retries the corrupted read;
                                        # it is not a provider's independently chosen recovery.
                                        gateway.queue.insert(
                                            3,
                                            tool(
                                                "query_availability",
                                                gateway.queue[2].tool_calls[0]["args"],
                                            ),
                                        )
                                else:
                                    gateway.queue.append(
                                        AIMessage(
                                            content="明确离线模型控制样例。"
                                            + (
                                                "证据正文 " * 7000
                                                if case_key == "T14-WRONG-EXPECTATION"
                                                else "未执行预约。"
                                            )
                                        )
                                    )
                            elif output and output.get("pending_draft") and not gateway.queue:
                                gateway.confirm(output)
                            run_execution(
                                engine,
                                redis,
                                settings,
                                identifier,
                                gateway_factory=lambda *_, g=gateway: g,
                            )
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                        save("worker-errors.json", errors)
                    worker_stop.wait(0.25)

            worker = threading.Thread(target=work, name="t14-offline-execution-runner", daemon=True)
            worker.start()
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
                "PYTHONPATH": str(PROJECT_ROOT / "backend"),
                "TEMP": str(PROJECT_ROOT / ".cache/tmp"),
                "TMP": str(PROJECT_ROOT / ".cache/tmp"),
                "AC_API_PROXY_TARGET": f"http://127.0.0.1:{args.port}",
            }

            def spawn(command, cwd, name):
                with (local / (name + ".log")).open("w", encoding="utf-8") as log:
                    p = subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=env,
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    processes.append(p)

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
            for _ in range(100):
                try:
                    if (
                        httpx.get(f"http://127.0.0.1:{args.port}/api/v1/health/live").status_code
                        == 200
                        and httpx.get(f"http://127.0.0.1:{args.ui_port}").status_code == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Lab HTTP services failed to start")
            review = {
                "mode": "offline_model_real_SQL_Redis_tools_HTTP_SSE",
                "api": f"http://127.0.0.1:{args.port}",
                "ui": f"http://127.0.0.1:{args.ui_port}/workspace",
                "failed_run": failed,
                "cases": ids,
                "fault_id": fault["id"],
                "version": runtime.version_id,
                "stop_file": str(stop_file),
                "database": engine.url.database,
                "processes": [p.pid for p in processes],
                "expires_after_seconds": args.review_seconds,
                "provider_calls": 0,
                "worker_boundary": (
                    "One local offline runner using the production execution path; "
                    "not a new Linux/Celery concurrency experiment."
                ),
            }
            save("review-session.json", review)
            (PROJECT_ROOT / ".local/t14/latest-review.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "review_ready": True,
                        "ui": review["ui"],
                        "evidence": str(evidence),
                        "provider_calls": 0,
                    }
                ),
                flush=True,
            )
            if args.test:
                test_env = {
                    **env,
                    "AC_E2E_BASE_URL": f"http://127.0.0.1:{args.ui_port}",
                    "AC_T14_PASSWORD": password,
                    "AC_T14_LAB": json.dumps(review),
                    "AC_E2E_EVIDENCE_DIR": str(evidence),
                    "npm_config_cache": str(PROJECT_ROOT / ".cache/npm"),
                }
                with (evidence / "ui-tests.txt").open("w", encoding="utf-8") as log:
                    result = subprocess.run(
                        [
                            "node",
                            "node_modules/@playwright/test/cli.js",
                            "test",
                            "tests/workspace.spec.ts",
                        ],
                        cwd=PROJECT_ROOT / "frontend",
                        env=test_env,
                        stdout=log,
                        stderr=log,
                    )
                assert result.returncode == 0, "UI acceptance failed; see preserved ui-tests.txt"
            if args.review:
                end = time.monotonic() + args.review_seconds
                while time.monotonic() < end and not stop_file.exists():
                    time.sleep(0.5)
            assert not errors, errors
        finally:
            worker_stop.set()
            with Session(engine) as session:
                run_ids = list(session.scalars(select(TestRun.run_uuid)))
            for identifier in run_ids:
                service.cancel(1, identifier)
            if worker:
                worker.join(timeout=20)
            reader = TraceReader(engine, settings)
            runs = []
            for identifier in run_ids:
                snapshot = reader.snapshot(1, identifier)
                items = []
                cursor = None
                while True:
                    page = reader.page(1, identifier, after=cursor, limit=100)
                    items.extend(page["items"])
                    if not page["has_more"]:
                        break
                    cursor = page["next_cursor"]
                save(identifier + "-events.json", items)
                save(identifier + "-snapshot.json", snapshot)
                runs.append(
                    {"run_id": identifier, "status": snapshot["status"], "events": len(items)}
                )
            with Session(engine) as session:
                for a in session.scalars(select(Artifact)):
                    target = evidence / "artifacts" / str(a.id)
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes((PROJECT_ROOT / a.relative_path).read_bytes())
                bookings = list(session.scalars(select(Booking)))
                save(
                    "business-state.json",
                    [
                        {"scope": b.scope_id, "status": b.status, "attendees": b.attendees}
                        for b in bookings
                    ],
                )
                sessions = list(session.scalars(select(TestExecution.session_id)))
            for p in reversed(processes):
                if p.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    ) if os.name == "nt" else p.terminate()
                    p.wait(timeout=10)
            if user_created:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    connection.execute(
                        text(f"DROP USER IF EXISTS '{sql_user}'@:host"), {"host": "%"}
                    )
            if runtime:
                for sid in sessions:
                    if redis.exists(runtime._key(sid)):
                        runtime.delete_test_session(1, sid)
            keys = list(redis.scan_iter(settings.executor_namespace + "*"))
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
                    "runs": runs,
                },
            )
    admin.dispose()
    redis.close()


if __name__ == "__main__":
    main()
