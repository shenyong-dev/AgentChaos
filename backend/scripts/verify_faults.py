"""T07 reproducible lab. --offline never calls a provider; --review keeps a temporary UI."""

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
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evidence.reader import TraceReader
from app.evidence.recorder import redact
from app.executors.budget import FAULT_AUTHORIZATION, authorization_limit
from app.executors.runner import run_execution
from app.executors.service import case_content
from app.models import TestCase, TestRun, User
from app.schemas.faults import STAGES

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from agent_test_support import OfflineGateway, tool  # noqa: E402
from test_database import disposable_database, migrate  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--real-model", action="store_true")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--review-seconds", type=int, default=3600)
    parser.add_argument("--image", default="agentchaos-t07:dev")
    parser.add_argument("--port", type=int, default=18007)
    parser.add_argument("--ui-port", type=int, default=5174)
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=["control", "F01", "F02", "F03", "F04"],
        default=["control", "F01", "F02", "F03", "F04"],
    )
    args = parser.parse_args()
    ports = [args.port] + ([args.ui_port] if args.ui or args.review else [])
    if len(set(ports)) != len(ports) or any(not 1024 <= p <= 65535 for p in ports):
        parser.error("Choose distinct unprivileged API/UI ports")
    if not 1 <= args.review_seconds <= 86400:
        parser.error("Review lifetime must be between 1 second and 24 hours")
    for port in ports:
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(
                f"Lab port {port} is occupied; no existing process or database was changed"
            ) from None
    temporary = PROJECT_ROOT / ".cache/tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ.update(TEMP=str(temporary), TMP=str(temporary))
    settings = Settings()
    if args.real_model:
        assert settings.executor_allow_paid_model_calls and authorization_limit(settings) == -1
        assert settings.executor_budget_authorization_id == FAULT_AUTHORIZATION
    if args.review and not args.offline:
        raise SystemExit("Review mode uses the explicit offline gateway only")
    marker = uuid4().hex[:12]
    namespace = "ac:t07:lab:" + marker
    settings = settings.model_copy(update={"executor_namespace": namespace, "run_concurrency": 2})
    local = PROJECT_ROOT / ".local/t07" / marker
    evidence = (
        PROJECT_ROOT / "docs/evidence/T07" / f"{'offline' if args.offline else 'linux'}-{marker}"
    )
    local.mkdir(parents=True)
    evidence.mkdir(parents=True)
    base = f"http://127.0.0.1:{args.port}"
    redis = Redis.from_url(settings.redis_url)
    ledger_key = f"ac:t05:budget:{FAULT_AUTHORIZATION}:agent"

    def ledger():
        return {k.decode(): int(v) for k, v in redis.hgetall(ledger_key).items()}

    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "mode": "offline_gateway_real_storage" if args.offline else "real_linux_deepseek",
        "notice": "Mechanically approved dev fixtures; no human review or T08 verdict",
        "ledger_before": ledger(),
        "scenarios": {},
    }

    def save(name, value):
        (evidence / name).write_text(
            json.dumps(redact(value, settings), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    docker_base = [
        r"D:\DockerDesktop\resources\bin\docker.exe",
        "--config",
        str(PROJECT_ROOT / ".local/t05/docker-config"),
        "--host",
        "npipe:////./pipe/dockerDesktopLinuxEngine",
    ]
    containers = []
    processes = []
    sessions = []

    def docker(*command, check=True):
        result = subprocess.run(
            [*docker_base, *command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        if check and result.returncode:
            (local / "docker-error.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
            raise RuntimeError("Docker failed; raw diagnostic is local only")
        return result

    def spawn(command, env, cwd, name):
        with (local / (name + ".log")).open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                env=env,
                cwd=cwd,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        processes.append(process)
        return process

    with disposable_database("T07") as engine:
        migrate(engine)
        password = settings.demo_seed_password.get_secret_value()
        assert password, "Local DEMO_SEED_PASSWORD is required, never printed or published"
        seed_database(engine, password)
        runtime = AgentRuntime(engine, redis, settings, auto_register=True)
        reader = TraceReader(engine, settings)
        sql_user = "ac_t07_" + marker
        sql_password = secrets.token_hex(24)
        admin = create_database_engine(settings, database="mysql")
        envfile = local / "worker.env"
        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(
                    text(f"CREATE USER '{sql_user}'@:host IDENTIFIED BY :password"),
                    {"host": "%", "password": sql_password},
                )
                c.exec_driver_sql(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{engine.url.database}`.* "
                    f"TO '{sql_user}'@'%%'"
                )
            worker_env = {
                "APP_ENV": "test",
                "MYSQL_HOST": "127.0.0.1" if args.offline else "host.docker.internal",
                "MYSQL_PORT": str(settings.mysql_port),
                "MYSQL_DATABASE": engine.url.database,
                "MYSQL_USER": sql_user,
                "MYSQL_PASSWORD": sql_password,
                "REDIS_HOST": "127.0.0.1" if args.offline else "host.docker.internal",
                "REDIS_PORT": str(settings.redis_port),
                "REDIS_PASSWORD": settings.redis_password.get_secret_value(),
                "REDIS_DB": str(settings.redis_db),
                "DEEPSEEK_API_KEY": settings.deepseek_api_key.get_secret_value(),
                "AGENT_MODEL": settings.agent_model,
                "AGENT_THINKING": "disabled",
                "AGENT_MAX_OUTPUT_TOKENS": str(settings.agent_max_output_tokens),
                "MODEL_TIMEOUT_SECONDS": str(settings.model_timeout_seconds),
                "MODEL_MAX_RETRIES": "0",
                "ALLOW_PAID_MODEL_CALLS": "false",
                "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false" if args.offline else "true",
                "EXECUTOR_BUDGET_AUTHORIZATION_ID": FAULT_AUTHORIZATION,
                "EXECUTOR_AGENT_BUDGET_UNLIMITED": "true",
                "EXECUTOR_AGENT_BUDGET_USD": "0",
                "EXECUTOR_NAMESPACE": namespace,
                "EXECUTOR_QUEUE": "t07-" + marker,
                "RUN_CONCURRENCY": "2",
                "EXECUTOR_LEASE_SECONDS": "45",
            }
            mounts = []
            for source, target, readonly in [
                ("backend/app", "backend/app", True),
                ("backend/alembic", "backend/alembic", True),
                (".local", ".local", False),
                (".cache/jieba", ".cache/jieba", False),
            ]:
                mounts += [
                    "--mount",
                    f"type=bind,source={PROJECT_ROOT / source},"
                    f"target=/workspace/AgentChaos/{target}" + (",readonly" if readonly else ""),
                ]

            def start(suffix, command, ports=None):
                name = "ac-t07-" + marker + "-" + suffix
                docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--label",
                    "agentchaos.lab=" + marker,
                    "--env-file",
                    str(envfile),
                    *mounts,
                    *(ports or []),
                    args.image,
                    *command,
                )
                containers.append(name)
                return name

            if args.offline:
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
                    {**os.environ, **worker_env},
                    PROJECT_ROOT / "backend",
                    "api",
                )
            else:
                envfile.write_text(
                    "\n".join(f"{k}={v}" for k, v in worker_env.items()) + "\n", encoding="utf-8"
                )
                start(
                    "api",
                    ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
                    ["-p", f"127.0.0.1:{args.port}:8000"],
                )
                for n in (1, 2):
                    start(
                        f"worker{n}",
                        [
                            "celery",
                            "-A",
                            "app.workers.celery_app:celery_app",
                            "worker",
                            "--loglevel=INFO",
                            "--concurrency=1",
                            f"--hostname=t07-worker{n}@%h",
                        ],
                    )
                start("dispatcher", ["python", "-m", "app.executors.dispatch"])
            client = httpx.Client(base_url=base, auth=("demo_operator", password), timeout=20)
            for _ in range(100):
                try:
                    if client.get("/api/v1/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.3)
            else:
                raise RuntimeError("Lab API not ready")

            def request(method, path, body=None):
                response = client.request(method, path, json=body)
                if response.is_error:
                    save("http-error.json", response.json())
                    response.raise_for_status()
                return response.json()["data"]

            utterance = "明天A101，14:00到15:00，10人，请生成完整待确认草稿，等我确认。"
            with Session(engine) as session, session.begin():
                reviewer = session.scalar(select(User).where(User.role == "reviewer"))
                case = TestCase(
                    case_key="T07-LAB-" + marker.upper(),
                    revision=1,
                    family_key="t07-development-booking",
                    category="tool_execution",
                    dataset_split="dev",
                    source="T07-explicit-offline-model-fixture"
                    if args.offline
                    else "T07-real-model-development-fixture",
                    turns_json=[{"user": utterance}],
                    fixture_json={
                        "fixture": "booking-default-v1",
                        "reference_clock": "2026-10-25T01:00:00+00:00"
                        if args.offline
                        else "2026-10-27T09:00:00+08:00",
                    },
                    expected_json={"assertions": [{"type": "booking_count", "equals": 1}]},
                    review_status="approved",
                    reviewer_id=reviewer.id,
                    reviewed_at=datetime.now(UTC),
                    content_hash="0" * 64,
                )
                case.content_hash = canonical_hash(case_content(case))
                session.add(case)
                session.flush()
                case_id = case.id
            for kind in args.scenarios:
                profile = None
                if kind != "control":
                    profile = request(
                        "POST",
                        "/api/v1/fault-profiles",
                        {
                            "profile_key": f"T07-{kind}-{marker}".upper(),
                            "revision": 1,
                            "fault_type": kind,
                            "trigger_stage": STAGES[kind],
                            "target_tool": "retrieve_rules" if kind == "F03" else "confirm_booking",
                            "parameters": {"trigger_on_nth": 1, "trigger_window": 1},
                            "seed": 27,
                            "max_triggers": 1,
                        },
                    )
                created = request(
                    "POST",
                    "/api/v1/runs",
                    {
                        "request_id": str(uuid4()),
                        "target_version_id": runtime.version_id,
                        "case_revision_ids": [case_id],
                        "fault_profile_id": profile["id"] if profile else None,
                        "seed": 27,
                        "limits": {
                            "concurrency": 1,
                            "timeout_seconds": 600,
                            "max_steps": 200,
                            "max_model_calls": 30,
                            "max_tokens": 800000,
                        },
                        "budgets": {"agent_microusd": 250000},
                    },
                )
                run = created["run_id"]
                execution = created["executions"][0]["execution_id"]
                sessions.append(created["executions"][0]["session_id"])
                report["scenarios"][kind] = {
                    "run_id": run,
                    "execution_id": execution,
                    "profile": profile,
                }
                gateway = OfflineGateway()
                if args.offline:
                    gateway.preview()
                    if kind == "F03":
                        gateway.queue.insert(
                            2, tool("retrieve_rules", {"query": "人数容量时间确认规则"})
                        )
                    run_execution(
                        engine,
                        redis,
                        settings,
                        execution,
                        gateway_factory=lambda *_, current=gateway: current,
                    )
                else:
                    request("POST", f"/api/v1/runs/{run}/dispatch")
                confirmed = False
                deadline = time.monotonic() + 500
                while time.monotonic() < deadline:
                    view = request("GET", f"/api/v1/runs/{run}")
                    e = view["executions"][0]
                    if e["status"] == "waiting_input" and not confirmed:
                        confirmed = True
                        draft = e["output"]["pending_draft"]
                        if args.offline:
                            gateway.confirm(e["output"])
                            if kind == "F04":
                                gateway.queue.insert(
                                    1,
                                    tool(
                                        "lookup_booking_by_request",
                                        {"idempotency_key": draft["suggested_idempotency_key"]},
                                    ),
                                )
                        request(
                            "POST",
                            f"/api/v1/runs/{run}/executions/{execution}/input",
                            {
                                "request_id": str(uuid4()),
                                "expected_revision": e["timing"]["revision"],
                                "text": "确认",
                            },
                        )
                        if args.offline:
                            run_execution(
                                engine,
                                redis,
                                settings,
                                execution,
                                gateway_factory=lambda *_, current=gateway: current,
                            )
                    elif e["status"] == "waiting_input" and confirmed:
                        assert kind in ("F01", "F02"), e
                        save(f"{kind}-waiting-after-fault.json", view)
                        view = request("POST", f"/api/v1/runs/{run}/cancel")
                        break
                    elif e["status"] in ("passed", "failed", "error", "inconclusive", "cancelled"):
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f"{kind} lab deadline reached")
                save(f"{kind}-run.json", view)
                found = []
                after = None
                while True:
                    page = request(
                        "GET", f"/api/v1/runs/{run}/events" + (f"?cursor={after}" if after else "")
                    )
                    found += page["items"]
                    after = page["next_cursor"]
                    if not page["has_more"]:
                        break
                save(f"{kind}-events.json", found)
                injected = [f["event"] for f in found if f["event"]["type"] == "fault.injected"]
                expected_bookings = None if kind == "F03" else (0 if kind in ("F01", "F02") else 1)
                assert len(injected) == (0 if kind == "control" else 1), (kind, "fault not applied")
                e = view["executions"][0]
                if expected_bookings is None:
                    assert len(e["after"]) in (0, 1), (kind, e)
                    # A model may stop/clarify after malformed read output. T07 checks
                    # injection, not a requirement to recover or an agent verdict.
                    json_result = injected[0]["payload"]["visible_result"]
                    try:
                        json.loads(json_result)
                    except json.JSONDecodeError:
                        pass
                    else:
                        raise AssertionError("F03 did not deliver malformed JSON")
                else:
                    assert len(e["after"]) == expected_bookings, (kind, e)
                if kind == "F02":
                    assert (
                        len(injected[0]["payload"]["rollback_probe"]) == 1
                        and not injected[0]["payload"]["after"]
                    )
                if kind == "F04":
                    payload = injected[0]["payload"]
                    key = payload["visible_result"]["request_key"]
                    assert (
                        payload["raw_result"]["commit_state"] == "committed"
                        and payload["visible_result"]["commit_state"] == "unknown"
                    )
                    assert any(
                        f["event"]["type"] == "tool.requested"
                        and f["event"]["payload"]["tool"] == "lookup_booking_by_request"
                        and f["event"]["payload"]["arguments"]["idempotency_key"] == key
                        for f in found
                    )
                report["scenarios"][kind].update(
                    booking_count=len(e["after"]),
                    faults_injected=len(injected),
                    status=e["status"],
                    events=len(found),
                    review_url=f"http://127.0.0.1:{args.ui_port}/runs/{run}",
                )
            report["checks"] = {
                "requested_effects_verified": True,
                "coverage": list(report["scenarios"]),
                "no_agent_verdict_claimed": True,
            }
            save("matrix.json", report)
            if args.offline:
                assert ledger() == report["ledger_before"], (
                    "Offline verification must not spend model budget"
                )
            if args.ui or args.review:
                ui_env = {
                    **os.environ,
                    "API_PROXY_TARGET": base,
                    "AC_E2E_BASE_URL": f"http://127.0.0.1:{args.ui_port}",
                    "AC_T07_LAB": json.dumps(report["scenarios"]),
                    "AC_T07_OFFLINE": "1" if args.offline else "0",
                    "AC_T06_DEMO_PASSWORD": password,
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
                    ui_env,
                    PROJECT_ROOT / "frontend",
                    "vite",
                )
                for _ in range(50):
                    try:
                        if httpx.get(f"http://127.0.0.1:{args.ui_port}").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                if args.ui:
                    ui = subprocess.run(
                        [
                            "node",
                            "node_modules/@playwright/test/cli.js",
                            "test",
                            "tests/faults.spec.ts",
                        ],
                        cwd=PROJECT_ROOT / "frontend",
                        env=ui_env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=120,
                    )
                    (evidence / "playwright.txt").write_text(
                        ui.stdout + ui.stderr, encoding="utf-8"
                    )
                    assert ui.returncode == 0, "Fault evidence UI verification failed"
                if args.review:
                    save("review-urls.json", report["scenarios"])
                    (PROJECT_ROOT / ".local/t07/latest-review.json").write_text(
                        json.dumps(report["scenarios"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(
                        json.dumps(
                            {
                                "review_ready": True,
                                "mode": "offline only, real MySQL/Redis/tools",
                                "account": "demo_operator",
                                "password": "Use local DEMO_SEED_PASSWORD; never printed",
                                "urls": {
                                    k: v["review_url"] for k, v in report["scenarios"].items()
                                },
                                "auto_cleanup_seconds": args.review_seconds,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    end = time.monotonic() + args.review_seconds
                    while time.monotonic() < end:
                        time.sleep(1)
            client.close()
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
            for name in reversed(containers):
                logs = docker("logs", name, check=False)
                raw = logs.stdout + logs.stderr
                for secret in (
                    settings.deepseek_api_key.get_secret_value(),
                    settings.redis_password.get_secret_value(),
                    sql_password,
                    password,
                ):
                    if secret:
                        raw = raw.replace(secret, "[REDACTED]")
                (evidence / (name + ".log.txt")).write_text(raw, encoding="utf-8")
                docker("rm", "-f", name, check=False)
            with Session(engine) as session:
                for run in session.scalars(select(TestRun.run_uuid)):
                    save(f"retained-{run}.json", reader.snapshot(1, run))
                    source = PROJECT_ROOT / ".local/t05/model-calls" / run
                    if source.exists():
                        shutil.copytree(source, evidence / "model-calls" / run, dirs_exist_ok=True)
                    source = PROJECT_ROOT / ".local/evidence" / run
                    if source.exists():
                        shutil.copytree(source, evidence / "artifacts" / run, dirs_exist_ok=True)
            for sid in sessions:
                runtime.saver.delete_thread(sid)
            keys = list(redis.scan_iter(match=namespace + "*"))
            if keys:
                redis.delete(*keys)
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.exec_driver_sql(f"DROP USER IF EXISTS '{sql_user}'@'%%'")
            admin.dispose()
            envfile.unlink(missing_ok=True)
            report.update(finished_at=datetime.now(UTC).isoformat(), ledger_after=ledger())
            save("final-report.json", report)
            save(
                "cleanup.json",
                {
                    "containers_removed": containers,
                    "review_processes_stopped": True,
                    "temporary_sql_user_removed": True,
                    "sessions_removed": sessions,
                    "global_ledgers_preserved": True,
                },
            )
    redis.close()
    print(
        json.dumps(
            {
                "evidence": str(evidence),
                "checks": report.get("checks"),
                "ledger": report["ledger_after"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
