"""Bounded real SQL/Redis/Linux execution, owned processes, no provider or ledger writes."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.versions import canonical_hash, register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.executors.service import RunService, case_content, frozen_case
from app.models import TestCase
from operations.workers import Docker, load
from operations.workers import start as start_workers
from operations.workers import stop as stop_workers

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from agent_test_support import REFERENCE  # noqa: E402
from test_database import migrate  # noqa: E402

from workflow_checks.storage import OwnedDatabase, export  # noqa: E402


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=10800)
    args = parser.parse_args()
    assert 60 <= args.seconds <= 43200
    assert shutil.disk_usage("C:/").free >= 2_000_000_000, "Low C space; no lab created"
    for port in (18022, 5189):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    root, base, marker = PROJECT_ROOT, Settings(), uuid4().hex[:12]
    local = root / ".local/t17h" / marker
    dest = root / "docs/evidence/T17H" / ("offline-" + marker)
    local.mkdir(parents=True)
    dest.mkdir(parents=True)
    profile = "t17h-" + marker
    workers = root / ".local/workers" / profile
    user, password = "ac_t17h_" + marker, uuid4().hex + uuid4().hex
    admin = create_database_engine(base, database="mysql")
    processes, created_user = [], False
    docker = Docker()
    own, storage = None, None

    def spawn(command, cwd, env, label):
        with (local / (label + ".log")).open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        processes.append(process)
        return process

    def close_owned():
        if load(workers).get("containers"):
            stop_workers(workers, docker)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)

    try:
        with ExitStack() as stack:
            storage = OwnedDatabase(local)
            engine = stack.enter_context(storage)
            stack.callback(close_owned)
            migrate(engine)
            seed_database(engine, base.demo_seed_password.get_secret_value())
            specs = [
                ("T17H-WELCOME", [{"user": "你好，只回复一句欢迎，不要预约。"}], 0),
                (
                    "T17H-CONFIRM",
                    [
                        {"user": "T17-BOOK 请预约明天14:00到15:00 A101 10人，先给草稿。"},
                        {"user": "确认"},
                    ],
                    1,
                ),
                (
                    "T17H-WAIT",
                    [{"user": "T17-BOOK 请预约明天14:00到15:00 A101 10人，先给草稿。"}],
                    0,
                ),
            ]
            ids = []
            with Session(engine) as db, db.begin():
                for key, turns, count in specs:
                    row = TestCase(
                        case_key=key,
                        revision=1,
                        family_key="T17H-free-mechanical-" + key,
                        category="normal",
                        dataset_split="dev",
                        source="development_fixture_not_human_approval",
                        turns_json=turns,
                        fixture_json={
                            "fixture": "booking-default-v1",
                            "reference_clock": REFERENCE.isoformat(),
                            "dataset": {
                                "knowledge_profile": "base",
                                "initial_state": {"active_bookings": []},
                                "independent_expectation": {
                                    "reason": "明确离线流程控制，非独立真人金标准或模型质量评估。",
                                    "active_booking_counts": [0, 1]
                                    if key == "T17H-WAIT"
                                    else [count],
                                },
                            },
                        },
                        expected_json={
                            "schema_version": "1.1" if key == "T17H-WAIT" else "1.0",
                            "assertions": [{"type": "unique_bookings"}]
                            if key == "T17H-WAIT"
                            else [{"type": "booking_count", "equals": count}],
                        },
                        review_status="approved",
                        reviewer_id=2,
                        reviewed_at=REFERENCE,
                        content_hash="0" * 64,
                    )
                    row.content_hash = canonical_hash(case_content(row))
                    frozen_case(row)
                    db.add(row)
                    db.flush()
                    ids.append(row.id)
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(
                    text(f"CREATE USER '{user}'@'%' IDENTIFIED BY :password"),
                    {"password": password},
                )
                created_user = True
                connection.execute(
                    text(
                        f"GRANT SELECT,INSERT,UPDATE,DELETE ON `{engine.url.database}`.* "
                        f"TO '{user}'@'%'"
                    )
                )
            own = base.model_copy(
                update={
                    "mysql_database": engine.url.database,
                    "mysql_user": user,
                    "mysql_password": SecretStr(password),
                    "deepseek_api_key": SecretStr(""),
                    "allow_paid_model_calls": False,
                    "executor_allow_paid_model_calls": False,
                    "casegen_allow_paid_model_calls": False,
                    "judge_allow_paid_model_calls": False,
                    "executor_agent_budget_usd": 0,
                    "executor_agent_budget_unlimited": False,
                    "executor_namespace": "ac:t17h:lab:" + marker,
                    "executor_queue": "t17h-" + marker,
                    "executor_lease_seconds": 10,
                }
            )
            version = register_version(engine, RunService(engine, own).current_manifest())
            values = own.model_dump(mode="json")
            for key in (
                "mysql_password",
                "redis_password",
                "deepseek_api_key",
                "demo_seed_password",
            ):
                values[key] = getattr(own, key).get_secret_value()
            env_values = {
                key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in values.items()
            }
            config = local / "lab.env"
            config.write_bytes(
                ("\n".join(f"{k}={v}" for k, v in env_values.items()) + "\n").encode()
            )
            config.chmod(0o600)
            start_workers(workers, profile, config, "agentchaos-t07:dev", docker, control=True)
            env = {
                **os.environ,
                **env_values,
                "PYTHONPATH": str(root / "backend"),
                "PYTHONIOENCODING": "utf-8",
                "AC_API_PROXY_TARGET": "http://127.0.0.1:18022",
            }
            spawn(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "workflow_checks.server:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18022",
                ],
                root / "backend",
                env,
                "api",
            )
            spawn(
                [
                    "D:/node.js/node.exe",
                    "node_modules/vite/bin/vite.js",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "5189",
                    "--strictPort",
                ],
                root / "frontend",
                env,
                "ui",
            )
            end = time.monotonic() + 60
            while time.monotonic() < end:
                try:
                    if (
                        httpx.get("http://127.0.0.1:18022/api/v1/health/live").status_code == 200
                        and httpx.get("http://127.0.0.1:5189").status_code == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.3)
            else:
                raise RuntimeError("Owned lab API/UI did not start")
            now = datetime.now(UTC)
            info = {
                "task": "T17H",
                "marker": marker,
                "profile": profile,
                "url": "http://127.0.0.1:5189/library",
                "api": "http://127.0.0.1:18022",
                "mode": "offline_test_double",
                "new_model_calls": 0,
                "version_id": version,
                "cases": dict(zip(("welcome", "confirm", "wait"), ids, strict=True)),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=args.seconds)).isoformat(),
                "stop_file": str(local / "stop-review"),
                "worker_state": str(workers / "state.json"),
                "config_file": str(config),
                "database": engine.url.database,
                "api_launcher_pid": processes[0].pid,
                "ui_pid": processes[1].pid,
                "evidence": str(dest),
                "local": str(local),
            }
            write(root / ".local/t17h/latest-review.json", info)
            write(
                dest / "review-session.json",
                {
                    k: v
                    for k, v in info.items()
                    if k not in {"config_file", "database", "local", "worker_state"}
                },
            )
            print(
                json.dumps(
                    {"started": True, "url": info["url"], "profile": profile, "new_model_calls": 0}
                ),
                flush=True,
            )
            until = time.monotonic() + args.seconds
            while time.monotonic() < until and not (local / "stop-review").exists():
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("Owned API/UI exited; keep private logs")
                if shutil.disk_usage("C:/").free < 2_000_000_000:
                    raise RuntimeError("Low C: space; stop owned lab without purging")
                time.sleep(1)
            close_owned()
            exported = export(engine, dest / "review-final-export.zip", files=workers / "data")
            write(dest / "review-final-export.json", exported)
            storage.export_verified = exported["verified"]

    finally:
        if load(workers).get("containers"):
            stop_workers(workers, docker)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
        cleanup_file = local / "storage-cleanup.json"
        safe_to_clean = (
            cleanup_file.exists() and json.loads(cleanup_file.read_bytes()).get("removed") is True
        )
        if own and safe_to_clean:
            with Redis.from_url(own.redis_url) as client:
                keys = list(client.scan_iter(own.executor_namespace + ":*"))
                if keys:
                    client.delete(*keys)
        if created_user and safe_to_clean:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text(f"DROP USER IF EXISTS '{user}'@'%'"))
        admin.dispose()


if __name__ == "__main__":
    main()
