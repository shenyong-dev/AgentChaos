"""T10 reviewed semantic relations in disposable real SQL/Redis.

Choose --real-model (separately authorized) or --offline (annotated infrastructure
double, never LLM understanding). Old budgets/evidence are never reset.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.core.config import PROJECT_ROOT, Settings
from app.core.passwords import hash_password
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evidence.reader import TraceReader
from app.evidence.recorder import redact
from app.executors.budget import METAMORPHIC_AUTHORIZATION
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.metamorphic.registration import register_catalog
from app.metamorphic.service import RelationService
from app.models import Artifact, User
from app.schemas.metamorphic import RelationCatalog
from app.schemas.runs import CreateRun

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from metamorphic_offline import make_gateway  # noqa: E402
from test_database import disposable_database, migrate  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--real-model", action="store_true")
    mode.add_argument("--offline", action="store_true")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--review-seconds", type=int, default=7200)
    parser.add_argument("--port", type=int, default=18011)
    parser.add_argument("--ui-port", type=int, default=5178)
    args = parser.parse_args()
    if not 1 <= args.review_seconds <= 86400:
        parser.error("review seconds must be 1..86400")
    if args.review or args.ui:
        assert args.port != args.ui_port and all(
            1024 <= p <= 65535 for p in (args.port, args.ui_port)
        )
        for port in (args.port, args.ui_port):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    raise SystemExit(f"Port {port} occupied; nothing changed") from None
    base = Settings()
    assert not base.allow_paid_model_calls and not base.executor_allow_paid_model_calls
    settings = base.model_copy(update={"executor_allow_paid_model_calls": args.real_model})
    redis = Redis.from_url(settings.redis_url, socket_timeout=15)
    key = f"ac:t05:budget:{METAMORPHIC_AUTHORIZATION}:agent"
    assert base.executor_budget_authorization_id == METAMORPHIC_AUTHORIZATION
    assert base.executor_agent_budget_unlimited
    marker_path = PROJECT_ROOT / ".local/t05" / f"{METAMORPHIC_AUTHORIZATION}.initialized.json"
    if args.real_model:
        assert marker_path.exists() and redis.hget(key, "limit") == b"-1", (
            "Never recreate lost ledger"
        )
    catalog_path = PROJECT_ROOT / "docs/fixtures/T10/relations.json"
    catalog = RelationCatalog.model_validate_json(catalog_path.read_bytes())
    receipt_path = catalog_path.with_name("human-review.json")
    receipt = json.loads(receipt_path.read_bytes())
    marker = uuid4().hex[:12]
    evidence = (
        PROJECT_ROOT / "docs/evidence/T10" / (("real-" if args.real_model else "offline-") + marker)
    )
    local = PROJECT_ROOT / ".local/t10" / marker
    evidence.mkdir(parents=True)
    local.mkdir(parents=True)
    settings = settings.model_copy(update={"executor_namespace": "ac:t10:" + marker})
    prior = json.loads((evidence.parent / "preparation.json").read_bytes())["old_ledgers"]

    def ledger(which):
        return {a.decode(): int(b) for a, b in redis.hgetall(which).items()}

    assert {k: ledger(k) for k in prior} == prior
    report = {
        "mode": "real_DeepSeek_SQL_Redis_in_process_executor"
        if args.real_model
        else "explicit_offline_annotated_double_real_SQL_Redis",
        "authorization": METAMORPHIC_AUTHORIZATION,
        "ledger_before": ledger(key),
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "scenarios": {},
        "executions": {},
    }

    def save(name, value):
        (evidence / name).write_text(
            json.dumps(redact(value, settings), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    save("independent-inputs.json", catalog.model_dump(mode="json"))
    save("human-review.json", receipt)
    processes, sessions = [], []
    sql_user = "ac_t10_" + marker
    sql_password = secrets.token_hex(24)
    admin = create_database_engine(base, database="mysql")
    user_created = False
    runtime = None
    with disposable_database("T10") as engine:
        migrate(engine)
        password = base.demo_seed_password.get_secret_value()
        assert password
        seed_database(engine, password)
        with Session(engine) as session, session.begin():
            session.add(
                User(
                    username="t10_other",
                    role="operator",
                    enabled=True,
                    password_hash=hash_password(password),
                )
            )
        runtime = AgentRuntime(
            engine,
            redis,
            settings,
            auto_register=True,
            prefix=settings.executor_namespace + ":session",
        )
        service = RunService(engine, settings)
        reader = TraceReader(engine, settings)
        relations = RelationService(engine)

        def export(run_id):
            save("run.json", reader.snapshot(1, run_id))
            save("relations.json", relations.read(1, run_id))
            events, cursor = [], None
            while True:
                page = reader.page(1, run_id, after=cursor)
                events.extend(page["items"])
                if not page["has_more"]:
                    break
                cursor = page["next_cursor"]
            save("events.json", events)
            with Session(engine) as session:
                run = service.owned_run(session, 1, run_id)
                for row in session.scalars(select(Artifact).where(Artifact.run_id == run.id)):
                    destination = evidence / "artifacts" / run_id / Path(row.relative_path).name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(PROJECT_ROOT / row.relative_path, destination)
            source = PROJECT_ROOT / ".local/t05/model-calls" / run_id
            if source.exists():
                shutil.copytree(source, evidence / "model-calls" / run_id, dirs_exist_ok=True)
            return len(events)

        try:
            ids = register_catalog(engine, catalog, receipt)
            run_id = service.create(
                1,
                CreateRun(
                    request_id=uuid4(),
                    target_version_id=runtime.version_id,
                    case_revision_ids=ids,
                    seed=2810,
                    budgets={"agent_microusd": 1_000_000},
                    limits={
                        "timeout_seconds": 900,
                        "max_steps": 500,
                        "max_model_calls": 100,
                        "max_tokens": 2_000_000,
                    },
                ),
            )
            report["run_id"] = run_id
            by_id = dict(zip(ids, catalog.members, strict=True))
            for execution in service.read(1, run_id)["executions"]:
                eid = execution["execution_id"]
                sessions.append(execution["session_id"])
                member = by_id[execution["case_revision_id"]]
                gateway = make_gateway(member) if args.offline else None
                kwargs = {"gateway_factory": lambda *_, g=gateway: g} if args.offline else {}
                result = run_execution(engine, redis, settings, eid, **kwargs)
                report["executions"][member.key] = {"execution_id": eid, "runner": result}
                report["events"] = export(run_id)
                save("matrix.json", report)
                print(json.dumps({"member": member.key, "runner": result}), flush=True)
            final = relations.read(1, run_id)
            report["summary"] = final["summary"]
            report["scenarios"] = {
                "relations": {
                    "run_id": run_id,
                    "review_url": f"http://127.0.0.1:{args.ui_port}/runs/{run_id}?view=relations",
                }
            }
            report["ledger_after"] = ledger(key)
            report["old_ledgers_unchanged"] = {k: ledger(k) for k in prior} == prior
            assert report["old_ledgers_unchanged"]
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
                    "EXECUTOR_NAMESPACE": settings.executor_namespace,
                    "PYTHONPATH": str(PROJECT_ROOT / "backend"),
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
                        shutil.which("node"),
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
                            httpx.get(
                                f"http://127.0.0.1:{args.port}/api/v1/health/live"
                            ).status_code
                            == 200
                            and httpx.get(f"http://127.0.0.1:{args.ui_port}").status_code == 200
                        ):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.3)
                else:
                    raise RuntimeError("Review server failed to start; inspect owned logs")
                if args.ui:
                    env.update(
                        AC_T10_LAB=json.dumps(report["scenarios"]),
                        AC_T10_DEMO_PASSWORD=password,
                        AC_E2E_EVIDENCE_DIR=str(evidence),
                        AC_E2E_BASE_URL=f"http://127.0.0.1:{args.ui_port}",
                    )
                    result_ui = subprocess.run(
                        [
                            shutil.which("node"),
                            "node_modules/@playwright/test/cli.js",
                            "test",
                            "metamorphic.spec.ts",
                        ],
                        cwd=PROJECT_ROOT / "frontend",
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                    )
                    (evidence / "playwright.txt").write_text(
                        result_ui.stdout + result_ui.stderr, encoding="utf-8"
                    )
                    report["UI_returncode"] = result_ui.returncode
                    save("matrix.json", report)
                if args.review:
                    latest = {
                        "evidence": evidence.relative_to(PROJECT_ROOT).as_posix(),
                        "database": engine.url.database,
                        "sql_user": sql_user,
                        "lifetime_seconds": args.review_seconds,
                        "paid_review": False,
                        "ready_at": datetime.now(UTC).isoformat(),
                        "expires_approximately": (
                            datetime.now(UTC) + timedelta(seconds=args.review_seconds)
                        ).isoformat(),
                        "stop_file": str(local / "stop-review"),
                        "scenarios": report["scenarios"],
                    }
                    (local.parent / "latest-review.json").write_text(
                        json.dumps(latest, indent=2) + "\n", encoding="utf-8"
                    )
                    save("review-session.json", latest)
                    print(
                        json.dumps(
                            {
                                "review_ready": True,
                                "evidence": latest["evidence"],
                                "cases": len(report["scenarios"]),
                            }
                        ),
                        flush=True,
                    )
                    until = time.monotonic() + args.review_seconds
                    while time.monotonic() < until and not (local / "stop-review").exists():
                        time.sleep(1)
        except BaseException as exc:
            save("failed-report.json", {**report, "error_type": type(exc).__name__})
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                    process.wait(timeout=10)
            if user_created:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    connection.execute(
                        text(f"DROP USER IF EXISTS '{sql_user}'@:host"), {"host": "%"}
                    )
            if runtime:
                for sid in sessions:
                    runtime.saver.delete_thread(sid)
            owned_keys = list(redis.scan_iter(match=settings.executor_namespace + ":*"))
            if owned_keys:
                redis.delete(*owned_keys)
            save(
                "cleanup.json",
                {
                    "owned_servers_stopped": True,
                    "owned_user_removed": True,
                    "owned_database_removed_on_context_exit": True,
                    "ledger": ledger(key),
                    "old_ledgers_unchanged": {k: ledger(k) for k in prior} == prior,
                },
            )
    admin.dispose()
    redis.close()
    print(json.dumps({"finished": True, "evidence": str(evidence)}), flush=True)


if __name__ == "__main__":
    main()
