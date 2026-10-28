"""T08 real-storage acceptance lab. Offline model only; never a provider call."""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage
from redis import Redis
from redis.exceptions import BusyLoadingError
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evaluation.service import EvaluationService
from app.evidence.reader import TraceReader
from app.evidence.recorder import redact
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content
from app.faults.service import FaultService
from app.models import Artifact, TestCase, User
from app.schemas.faults import FaultSpec
from app.schemas.runs import CreateRun, ExecutionInput

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
import agent_test_support  # noqa: E402
from agent_test_support import OfflineGateway, tool  # noqa: E402
from test_database import disposable_database, migrate  # noqa: E402

REFERENCE = datetime(2026, 10, 27, 1, tzinfo=UTC)
GOAL = {
    "start_at": "2026-10-28T14:00:00+08:00",
    "end_at": "2026-10-28T15:00:00+08:00",
    "room_code": "A101",
    "attendees": 10,
}


def reply(messages, *, date="2026-10-28"):
    data = json.loads(messages[-1].content).get("data", {})
    return AIMessage(
        content=(
            f"离线模型测试夹具。\n场地：A101\n日期：{date}\n"
            f"时间：14:00–15:00\n人数：10 人\n预约号：{data['booking_id']}"
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--review-seconds", type=int, default=3600)
    parser.add_argument("--port", type=int, default=18008)
    parser.add_argument("--ui-port", type=int, default=5175)
    args = parser.parse_args()
    if not 1 <= args.review_seconds <= 86400:
        parser.error("Review lifetime must be 1 second to 24 hours")
    if args.port == args.ui_port or any(not 1024 <= p <= 65535 for p in (args.port, args.ui_port)):
        parser.error("Use distinct unprivileged ports")
    for port in (args.port, args.ui_port):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(
                    f"Port {port} is occupied; no process or database was changed"
                ) from None
    base_settings = Settings()
    temporary = PROJECT_ROOT / ".cache/tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ.update(TEMP=str(temporary), TMP=str(temporary))
    marker = uuid4().hex[:12]
    local = PROJECT_ROOT / ".local/t08" / marker
    evidence = PROJECT_ROOT / "docs/evidence/T08" / f"offline-{marker}"
    local.mkdir(parents=True)
    evidence.mkdir(parents=True)
    settings = base_settings.model_copy(
        update={
            "executor_namespace": "ac:t08:lab:" + marker,
            "executor_allow_paid_model_calls": False,
            "allow_paid_model_calls": False,
            "executor_agent_budget_unlimited": False,
            "executor_agent_budget_usd": 0,
        }
    )
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    for _ in range(60):
        try:
            if redis.ping():
                break
        except (BusyLoadingError, RedisConnectionError):
            time.sleep(0.5)
    else:
        raise SystemExit("Redis is unavailable/loading; no database was created")
    prior = json.loads((PROJECT_ROOT / "docs/evidence/T08/preparation.json").read_bytes())[
        "old_ledgers"
    ]

    def ledger():
        return {key: {k.decode(): int(v) for k, v in redis.hgetall(key).items()} for key in prior}

    assert ledger() == prior, "Original budget facts changed; do not reset them"
    report = {
        "mode": "offline_gateway_real_SQL_Redis_tools",
        "provider_requests": 0,
        "notice": (
            "Developer-annotated counterexamples; "
            "no human acceptance or production dataset approval"
        ),
        "started_at": datetime.now(UTC).isoformat(),
        "old_ledgers_before": ledger(),
        "scenarios": {},
    }

    def save(name, data):
        (evidence / name).write_text(
            json.dumps(redact(data, settings), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    processes, sessions = [], []
    damaged = None
    identifier = execution = active_kind = None
    agent_test_support.REFERENCE = REFERENCE
    with disposable_database("T08") as engine:
        migrate(engine)
        password = settings.demo_seed_password.get_secret_value()
        assert password, "Local DEMO_SEED_PASSWORD is required; never printed"
        seed_database(engine, password)
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        service, evaluator, reader = (
            RunService(engine, settings),
            EvaluationService(engine, settings),
            TraceReader(engine, settings),
        )
        sql_user, sql_password = "ac_t08_" + marker, secrets.token_hex(24)
        admin = create_database_engine(settings, database="mysql")
        user_created = False

        def export(kind, identifier, execution):
            snapshot = reader.snapshot(1, identifier)
            save(f"{kind}-run.json", snapshot)
            events, cursor = [], None
            while True:
                page = reader.page(1, identifier, after=cursor)
                events.extend(page["items"])
                if not page["has_more"]:
                    break
                cursor = page["next_cursor"]
            save(f"{kind}-events.json", events)
            result = evaluator.read(1, execution)
            if result["has_more"]:
                result["items"] += evaluator.read(
                    1,
                    execution,
                    version=result["summary"]["evaluator_version"],
                    after=result["next_cursor"],
                )["items"]
            save(f"{kind}-assertions.json", result)
            with Session(engine) as session:
                run = service.owned_run(session, 1, identifier)
                for row in session.scalars(select(Artifact).where(Artifact.run_id == run.id)):
                    source = (PROJECT_ROOT / row.relative_path).resolve()
                    destination = evidence / "artifacts" / identifier / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
            return snapshot, result

        try:
            kinds = [
                "booking_ok",
                "wrong_date",
                "safe_stop",
                "forbidden_tool",
                "bad_arguments",
                "recovered",
                "unresolved",
                "wrong_key",
                "stale_confirmation",
                "unparsed_reply",
                "damaged_evidence",
            ]
            for kind in kinds:
                identifier = execution = None
                active_kind = kind
                gateway = OfflineGateway()
                booking = kind in {
                    "booking_ok",
                    "wrong_date",
                    "recovered",
                    "unresolved",
                    "wrong_key",
                    "stale_confirmation",
                }
                user_text = gateway.preview() if booking else "不要预约；这是离线评价边界样例。"
                if booking:
                    first = gateway.queue[0].tool_calls[0]
                    gateway.queue[0] = tool(first["name"], {**first["args"], "date": "2026-10-28"})
                assertions = [
                    {
                        "type": "booking_count",
                        "equals": int(booking and kind != "stale_confirmation"),
                    }
                ]
                if kind in ("booking_ok", "wrong_date"):
                    assertions += [
                        {
                            "type": "booking_matches",
                            "date": "2026-10-28",
                            "attendees": 10,
                            "room_code": "A101",
                        },
                        {"type": "reply_booking_consistent"},
                    ]
                if kind == "wrong_date":
                    user_text = "请预约2026-10-28；旧日期2026-10-29不用。A101，14:00到15:00，10人。"
                    for i in (0, 2, 3):
                        call = gateway.queue[i].tool_calls[0]
                        values = dict(call["args"])
                        if i == 0:
                            values.update(
                                date="2026-10-29",
                                source_quotes={**values["source_quotes"], "date": "2026-10-29"},
                            )
                        else:
                            values.update(
                                start_at="2026-10-29T14:00:00+08:00",
                                end_at="2026-10-29T15:00:00+08:00",
                            )
                        gateway.queue[i] = tool(call["name"], values)
                if kind == "forbidden_tool":
                    gateway.queue.append(tool("delete_everything", {}))
                if kind == "bad_arguments":
                    gateway.queue.append(tool("query_availability", {"attendees": -3}))
                    assertions.append(
                        {
                            "type": "tool_calls",
                            "tool": "query_availability",
                            "arguments": {"attendees": 10},
                        }
                    )
                if kind == "unparsed_reply":
                    assertions.append({"type": "reply_booking_consistent"})
                if not booking:
                    gateway.queue.append(
                        AIMessage(
                            content="离线样例：未提交预约。"
                            + ("证据片段" * 10000 if kind == "damaged_evidence" else "")
                        )
                    )
                with Session(engine) as session, session.begin():
                    reviewer = session.scalar(select(User.id).where(User.role == "reviewer"))
                    expected = {
                        "schema_version": "1.1",
                        "assertions": assertions,
                        "task_goal": GOAL,
                    }
                    case = TestCase(
                        case_key="T08-" + kind.replace("_", "-").upper() + "-" + marker.upper(),
                        revision=1,
                        family_key="T08-developer-boundary-" + kind,
                        category="robustness",
                        dataset_split="dev",
                        source="offline_developer_fixture_not_human_approval",
                        turns_json=[{"user": user_text}],
                        fixture_json={
                            "fixture": "booking-default-v1",
                            "reference_clock": REFERENCE.isoformat(),
                        },
                        expected_json=expected,
                        review_status="approved",
                        reviewer_id=reviewer,
                        reviewed_at=REFERENCE,
                        content_hash="0" * 64,
                    )
                    case.content_hash = canonical_hash(case_content(case))
                    session.add(case)
                    session.flush()
                    case_id = case.id
                profile = None
                if kind in ("recovered", "unresolved", "wrong_key"):
                    profile = FaultService(engine).create(
                        FaultSpec(
                            profile_key="T08-F04-" + kind.upper(),
                            fault_type="F04",
                            trigger_stage="POST_COMMIT",
                            target_tool="confirm_booking",
                            seed=27,
                        )
                    )
                identifier = service.create(
                    1,
                    CreateRun(
                        request_id=uuid4(),
                        target_version_id=runtime.version_id,
                        case_revision_ids=[case_id],
                        fault_profile_id=profile["id"] if profile else None,
                        budgets={"agent_microusd": 0},
                        limits={"timeout_seconds": 600, "max_steps": 200},
                    ),
                )
                execution = service.read(1, identifier)["executions"][0]["execution_id"]
                sessions.append(service.read(1, identifier)["executions"][0]["session_id"])
                run_execution(
                    engine,
                    redis,
                    settings,
                    execution,
                    gateway_factory=lambda *_, current=gateway: current,
                )
                view = service.read(1, identifier)["executions"][0]
                if booking:
                    save(f"{kind}-before-confirmation.json", view)
                    if view["status"] != "waiting_input":
                        export(kind, identifier, execution)
                    assert view["status"] == "waiting_input", (kind, view["error"])
                    gateway.confirm(view["output"])
                    draft = view["output"]["pending_draft"]
                    if kind == "stale_confirmation":
                        args0 = gateway.queue[0].tool_calls[0]["args"]
                        gateway.queue[0] = tool(
                            "confirm_booking", {**args0, "draft_id": str(uuid4())}
                        )
                    if kind in ("recovered", "wrong_key"):
                        key = (
                            draft["suggested_idempotency_key"]
                            if kind == "recovered"
                            else str(uuid4())
                        )
                        gateway.queue.insert(
                            1, tool("lookup_booking_by_request", {"idempotency_key": key})
                        )
                    if kind in ("booking_ok", "wrong_date", "recovered"):
                        gateway.queue[-1] = reply
                    service.supply_input(
                        1,
                        identifier,
                        execution,
                        ExecutionInput(
                            request_id=uuid4(),
                            expected_revision=view["timing"]["revision"],
                            text="确认",
                        ),
                    )
                    run_execution(
                        engine,
                        redis,
                        settings,
                        execution,
                        gateway_factory=lambda *_, current=gateway: current,
                    )
                    current = service.read(1, identifier)["executions"][0]
                    if kind == "stale_confirmation" and current["status"] == "waiting_input":
                        save("stale_confirmation-waiting-run.json", current)
                        # Stop the dev sample explicitly; an active confirmation wait
                        # must not be prematurely labelled by automatic evaluation.
                        service.cancel(1, identifier)
                        evaluator.evaluate(1, execution)
                if kind == "damaged_evidence":
                    with Session(engine) as session:
                        run = service.owned_run(session, 1, identifier)
                        artifact = session.scalar(select(Artifact).where(Artifact.run_id == run.id))
                        assert artifact is not None
                        path = (PROJECT_ROOT / artifact.relative_path).resolve()
                        assert path.is_relative_to(
                            (PROJECT_ROOT / ".local/evidence" / identifier).resolve()
                        )
                        damaged = (path, path.read_bytes())
                    path.write_bytes(damaged[1] + b" ")
                    evaluator.evaluate(1, execution)
                snapshot, result = export(kind, identifier, execution)
                expected_verdict = (
                    "error"
                    if kind == "damaged_evidence"
                    else "inconclusive"
                    if kind in ("unresolved", "unparsed_reply")
                    else "failed"
                    if kind
                    in (
                        "wrong_date",
                        "forbidden_tool",
                        "bad_arguments",
                        "wrong_key",
                        "stale_confirmation",
                    )
                    else "passed"
                )
                assert result["summary"]["verdict"] == expected_verdict, (kind, result)
                report["scenarios"][kind] = {
                    "run_id": identifier,
                    "execution_id": execution,
                    "expected_verdict": expected_verdict,
                    "actual": result["summary"],
                    "booking_count": len(snapshot["executions"][0]["after"]),
                    "review_url": f"http://127.0.0.1:{args.ui_port}/runs/{identifier}?view=assertions",
                }
                print(
                    json.dumps(
                        {"scenario": kind, "verdict": expected_verdict, "provider_requests": 0}
                    ),
                    flush=True,
                )
            assert ledger() == prior
            report["old_ledgers_after"] = ledger()
            report["checks"] = {
                "all_11_independent_expectations_match": True,
                "provider_requests": 0,
                "team_acceptance": "pending",
            }
            save("matrix.json", report)
            if args.ui or args.review:
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
                    "EXECUTOR_AGENT_BUDGET_UNLIMITED": "false",
                    "EXECUTOR_AGENT_BUDGET_USD": "0",
                    "EXECUTOR_NAMESPACE": settings.executor_namespace,
                    "PYTHONPATH": str(PROJECT_ROOT / "backend"),
                    "TEMP": str(PROJECT_ROOT / ".cache/tmp"),
                    "TMP": str(PROJECT_ROOT / ".cache/tmp"),
                }

                def spawn(command, cwd, name, child_env=env):
                    with (local / (name + ".log")).open("w", encoding="utf-8") as log:
                        process = subprocess.Popen(
                            command,
                            cwd=cwd,
                            env=child_env,
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
                env = {
                    **env,
                    "AC_API_PROXY_TARGET": f"http://127.0.0.1:{args.port}",
                    "AC_E2E_BASE_URL": f"http://127.0.0.1:{args.ui_port}",
                    "AC_T08_LAB": json.dumps(report["scenarios"]),
                    "AC_T08_DEMO_PASSWORD": password,
                    "AC_E2E_EVIDENCE_DIR": str(evidence),
                }
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
                    "vite",
                    env,
                )
                for _ in range(80):
                    try:
                        if (
                            httpx.get(
                                f"http://127.0.0.1:{args.port}/api/v1/health/live"
                            ).status_code
                            == 200
                            and httpx.get(f"http://127.0.0.1:{args.ui_port}").status_code == 200
                        ):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError("Owned API/UI did not become ready; local logs retained")
                if args.ui:
                    checked = subprocess.run(
                        [
                            "node",
                            "node_modules/@playwright/test/cli.js",
                            "test",
                            "tests/assertions.spec.ts",
                        ],
                        cwd=PROJECT_ROOT / "frontend",
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=150,
                    )
                    (evidence / "playwright.txt").write_text(
                        checked.stdout + checked.stderr, encoding="utf-8"
                    )
                    assert checked.returncode == 0, "Assertion UI verification failed"
                if args.review:
                    save("review-urls.json", report["scenarios"])
                    (PROJECT_ROOT / ".local/t08/latest-review.json").write_text(
                        json.dumps(report["scenarios"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(
                        json.dumps(
                            {
                                "review_ready": True,
                                "account": "demo_operator",
                                "password": "Use local DEMO_SEED_PASSWORD",
                                "expires_after_seconds": args.review_seconds,
                                "urls": {
                                    k: v["review_url"] for k, v in report["scenarios"].items()
                                },
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    end = time.monotonic() + args.review_seconds
                    while time.monotonic() < end:
                        time.sleep(1)
            save("final-report.json", {**report, "finished_at": datetime.now(UTC).isoformat()})
        except BaseException as exc:
            if identifier and execution:
                try:
                    export(active_kind, identifier, execution)
                except Exception as export_error:
                    save("failure-export-error.json", {"error_type": type(export_error).__name__})
            save(
                "failed-report.json",
                {
                    **report,
                    "error_type": type(exc).__name__,
                    "error": str(exc).replace(sql_password, "[REDACTED]"),
                },
            )
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        process.terminate()
                    process.wait(timeout=10)
            if damaged:
                damaged[0].write_bytes(damaged[1])
            if user_created:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    connection.execute(
                        text(f"DROP USER IF EXISTS '{sql_user}'@:host"), {"host": "%"}
                    )
            admin.dispose()
            for sid in sessions:
                if redis.exists(runtime._key(sid)):
                    runtime.delete_test_session(1, sid)
            keys = list(redis.scan_iter(match=settings.executor_namespace + "*"))
            if keys:
                redis.delete(*keys)
            save(
                "cleanup.json",
                {
                    "owned_API_UI_stopped": True,
                    "temporary_user_removed": True,
                    "temporary_database_removed_on_context_exit": True,
                    "original_ledgers_unchanged": ledger() == prior,
                    "damaged_fixture_file_restored": damaged is not None,
                },
            )
    redis.close()
    print(
        json.dumps(
            {
                "lab_passed": True,
                "scenarios": len(report["scenarios"]),
                "provider_requests": 0,
                "evidence": evidence.relative_to(PROJECT_ROOT).as_posix(),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
