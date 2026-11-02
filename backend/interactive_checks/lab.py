"""Own disposable DB/user/API/UI with explicit offline or approved T17D budget."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from redis import Redis
from sqlalchemy import text

from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.executors.service import RunService
from app.interactive.budget import AUTHORIZATION, GLOBAL_LIMIT

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from test_database import disposable_database, migrate  # noqa:E402


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--seconds", type=int, default=21600)
    parser.add_argument("--init-approved-budget", action="store_true")
    args = parser.parse_args()
    assert 60 <= args.seconds <= 86400
    for port in (18021, 5188):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    root, settings = PROJECT_ROOT, Settings()
    assert shutil.disk_usage("C:/").free > 2_000_000_000
    marker = uuid4().hex[:12]
    local = root / ".local/t17d" / marker
    dest = root / "docs/evidence/T17D" / (("offline-" if args.offline else "real-") + marker)
    local.mkdir(parents=True)
    stop = local / "stop-review"
    client = Redis.from_url(settings.redis_url)
    admin = create_database_engine(settings, database="mysql")
    user, password = "ac_t17d_" + marker, uuid4().hex + uuid4().hex
    processes, created_user = [], False
    baseline = json.loads((root / "docs/evidence/T17D/preparation.json").read_bytes())

    def old_ledgers():
        return {
            key: {k.decode(): int(v) for k, v in client.hgetall(key).items()}
            for key in baseline["ledgers"]
        }

    assert old_ledgers() == baseline["ledgers"]

    def start(command, cwd, env, label):
        with (local / (label + ".log")).open("w", encoding="utf-8") as log:
            p = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        processes.append(p)
        return p

    try:
        with disposable_database("T17D") as engine:
            migrate(engine)
            seed_database(engine, settings.demo_seed_password.get_secret_value())
            own = settings.model_copy(update={"mysql_database": engine.url.database})
            manifest = RunService(engine, own).current_manifest()
            version = register_version(engine, manifest)
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(
                    text(f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY :password"),
                    {"password": password},
                )
                created_user = True
                c.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{engine.url.database}`.* "
                        f"TO '{user}'@'127.0.0.1'"
                    )
                )
            if not args.offline:
                assert settings.deepseek_api_key.get_secret_value()
                key = f"ac:t17d:budget:{AUTHORIZATION}:agent"
                initialization = "ac:t17d:authorization:" + AUTHORIZATION
                if args.init_approved_budget:
                    initialized = client.eval(
                        "if redis.call('EXISTS',KEYS[1],KEYS[2])~=0 then return 0 end "
                        "redis.call('SET',KEYS[2],ARGV[1]); "
                        "redis.call('HSET',KEYS[1],'limit',ARGV[2],'held_upper',0,"
                        "'charged_estimate',0,'calls_started',0,'calls_completed',0); return 1",
                        2,
                        key,
                        initialization,
                        "user-approved-2026-11-02",
                        GLOBAL_LIMIT,
                    )
                    assert initialized == 1, "Never reset an existing approval or missing ledger"
                assert client.exists(key) and client.exists(initialization), (
                    "Missing ledger: fail closed"
                )
            env = {
                **os.environ,
                "PYTHONPATH": str(root / "backend"),
                "PYTHONIOENCODING": "utf-8",
                "MYSQL_DATABASE": engine.url.database,
                "MYSQL_USER": user,
                "MYSQL_PASSWORD": password,
                "ALLOW_PAID_MODEL_CALLS": "false" if args.offline else "true",
                "MODEL_BUDGET_AUTHORIZATION_ID": AUTHORIZATION,
                "AGENT_BUDGET_USD": "1",
                "AGENT_MAX_OUTPUT_TOKENS": "1024",
                "MODEL_TIMEOUT_SECONDS": "30",
                "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
                "JUDGE_ALLOW_PAID_MODEL_CALLS": "false",
                "AC_T17D_OFFLINE": "1" if args.offline else "0",
                "AC_T17D_CONTROL_FILE": str(local / "receipt-controls.json"),
                "AC_API_PROXY_TARGET": "http://127.0.0.1:18021",
            }
            if args.offline:
                env["DEEPSEEK_API_KEY"] = ""
            start(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "interactive_checks.server:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18021",
                ],
                root / "backend",
                env,
                "api",
            )
            start(
                [
                    "D:/node.js/node.exe",
                    "node_modules/vite/bin/vite.js",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "5188",
                    "--strictPort",
                ],
                root / "frontend",
                env,
                "ui",
            )
            for _ in range(100):
                try:
                    if (
                        httpx.get("http://127.0.0.1:18021/api/v1/health/live").status_code == 200
                        and httpx.get("http://127.0.0.1:5188").status_code == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Owned lab did not start")
            now = datetime.now(UTC)
            info = {
                "marker": marker,
                "url": "http://127.0.0.1:5188/interactive",
                "api": "http://127.0.0.1:18021",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=args.seconds)).isoformat(),
                "stop_file": str(stop),
                "mode": "offline_control" if args.offline else "real_provider",
                "version_id": version,
                "config_hash": manifest["config_hash"],
                "no_worker": True,
            }
            write(dest / "review-session.json", info)
            write(
                root / ".local/t17d/latest-review.json",
                {
                    **info,
                    "database": engine.url.database,
                    "evidence": dest.relative_to(root).as_posix(),
                    "pids": [p.pid for p in processes],
                    "control_file": str(local / "receipt-controls.json"),
                },
            )
            print(json.dumps(info), flush=True)
            finish = time.monotonic() + args.seconds
            try:
                while time.monotonic() < finish and not stop.exists():
                    assert all(p.poll() is None for p in processes)
                    assert old_ledgers() == baseline["ledgers"]
                    assert shutil.disk_usage("C:/").free > 2_000_000_000, (
                        "Low C drive: stop, no purge"
                    )
                    time.sleep(2)
            finally:
                sessions = []
                with engine.connect() as c:
                    scopes = (
                        c.execute(
                            text(
                                "SELECT DISTINCT scope_id FROM sb_rooms "
                                "WHERE scope_id LIKE 'test:interactive:%'"
                            )
                        )
                        .scalars()
                        .all()
                    )
                with httpx.Client(
                    base_url=info["api"],
                    timeout=30,
                    auth=("demo_operator", settings.demo_seed_password.get_secret_value()),
                ) as api:
                    for scope in scopes:
                        sid = str(UUID(scope.rsplit(":", 1)[-1]))
                        item = {"session_id": sid}
                        for label, suffix in [
                            ("view", ""),
                            ("history", "/history?limit=100"),
                            ("trace", "/trace?limit=100"),
                        ]:
                            try:
                                response = api.get("/api/v1/interactive/sessions/" + sid + suffix)
                                item[label] = {
                                    "http_status": response.status_code,
                                    "body": response.json(),
                                }
                            except httpx.HTTPError:
                                item[label] = {"status": "unavailable"}
                        sessions.append(item)
                write(dest / "final-sessions.json", sessions)
                for p in reversed(processes):
                    if p.poll() is None:
                        p.terminate()
                        p.wait(timeout=15)
                # Export owned business facts before the disposable database is removed.
                with engine.connect() as c:
                    dump = {
                        t: [dict(x) for x in c.exec_driver_sql(f"SELECT * FROM `{t}`").mappings()]
                        for t in (
                            "sb_rooms",
                            "sb_booking_drafts",
                            "sb_bookings",
                            "sb_booking_slots",
                        )
                    }
                write(dest / "final-sql.json", dump)
                if created_user:
                    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                        c.execute(text(f"DROP USER '{user}'@'127.0.0.1'"))
                    created_user = False
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=15)
        if created_user:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(text(f"DROP USER '{user}'@'127.0.0.1'"))
        write(
            dest / "cleanup.json",
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "old_ledgers_unchanged": old_ledgers() == baseline["ledgers"],
                "budget_preserved": True,
            },
        )
        client.close()
        admin.dispose()


if __name__ == "__main__":
    main()
