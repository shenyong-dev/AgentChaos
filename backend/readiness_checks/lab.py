"""Real MySQL/Redis/control-channel checks; no tasks, model requests or ledger writes."""

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from redis import Redis
from sqlalchemy import text

from app.agents.versions import canonical_hash, register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.executors.service import RunService
from app.services.readiness import collect_readiness, database_checks, worker_check

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from test_database import disposable_database, migrate  # noqa:E402


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ledgers(client):
    keys = json.loads((PROJECT_ROOT / "docs/evidence/T17C/preparation.json").read_bytes())[
        "ledgers"
    ]
    return {
        key: {k.decode(): int(v) for k, v in client.hgetall(key).items()} for key in sorted(keys)
    }


def sql_snapshot(engine):
    with engine.connect() as c:
        tables = c.execute(text("SHOW TABLES")).scalars().all()
        return {
            t: canonical_hash(
                json.loads(
                    json.dumps(
                        [dict(row) for row in c.exec_driver_sql(f"SELECT * FROM `{t}`").mappings()],
                        default=str,
                    )
                )
            )
            for t in sorted(tables)
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=21600)
    args = parser.parse_args()
    assert 1 <= args.seconds <= 86400
    root, s = PROJECT_ROOT, Settings()
    for port in (18020, 5187, 16381):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    assert not any(
        getattr(s, k)
        for k in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    assert shutil.disk_usage("C:/").free > 2_000_000_000
    marker = uuid4().hex[:12]
    local = root / ".local/t17c" / marker
    dest = root / "docs/evidence/T17C" / ("lab-" + marker)
    local.mkdir(parents=True)
    stop = local / "stop-review"
    admin = create_database_engine(s, database="mysql")
    client = Redis.from_url(s.redis_url)
    before = ledgers(client)
    expected = json.loads((root / "docs/evidence/T17C/preparation.json").read_bytes())["ledgers"]
    assert before == expected and len(before) == 13
    user, password = "ac_t17c_r_" + marker, uuid4().hex + uuid4().hex
    processes, created, observations = [], False, {}

    def start(command, cwd, env, label):
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

    def close(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)

    try:
        with disposable_database("T17C") as engine:
            migrate(engine)
            seed_database(engine, s.demo_seed_password.get_secret_value())
            own = s.model_copy(
                update={
                    "mysql_database": engine.url.database,
                    "deepseek_api_key": "",
                    "executor_namespace": "ac:t17c:" + marker,
                    "executor_queue": "t17c-" + marker,
                }
            )
            # model_copy does not validate SecretStr; use the declared type here.
            from pydantic import SecretStr

            own.deepseek_api_key = SecretStr("")
            observations["source_not_registered"] = [
                c.model_dump(mode="json") for c in database_checks(own)
            ]
            assert observations["source_not_registered"][1]["status"] == "blocked"
            manifest = RunService(engine, own).current_manifest()
            register_version(engine, manifest)
            with engine.begin() as c:
                revision = c.scalar(text("SELECT version_num FROM alembic_version"))
                c.execute(text("UPDATE alembic_version SET version_num='t17c_control_missing'"))
            observations["migration_mismatch"] = [
                c.model_dump(mode="json") for c in database_checks(own)
            ]
            assert observations["migration_mismatch"][0]["status"] == "blocked"
            with engine.begin() as c:
                c.execute(text("UPDATE alembic_version SET version_num=:v"), {"v": revision})
            snapshot = sql_snapshot(engine)
            try:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                    c.execute(
                        text(f"CREATE USER '{user}'@:host IDENTIFIED BY :password"),
                        {"host": "%", "password": password},
                    )
                    created = True
                    c.execute(
                        text(f"GRANT SELECT ON `{engine.url.database}`.* TO '{user}'@:host"),
                        {"host": "%"},
                    )
                read_settings = own.model_copy(
                    update={"mysql_user": user, "mysql_password": SecretStr(password)}
                )
                env = {
                    **os.environ,
                    "PYTHONPATH": str(root / "backend"),
                    "PYTHONIOENCODING": "utf-8",
                    "MYSQL_DATABASE": engine.url.database,
                    "MYSQL_USER": user,
                    "MYSQL_PASSWORD": password,
                    "DEEPSEEK_API_KEY": "",
                    "ALLOW_PAID_MODEL_CALLS": "false",
                    "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                    "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
                    "JUDGE_ALLOW_PAID_MODEL_CALLS": "false",
                    "EXECUTOR_QUEUE": own.executor_queue,
                    "EXECUTOR_NAMESPACE": own.executor_namespace,
                    "AC_API_PROXY_TARGET": "http://127.0.0.1:18020",
                }
                for key in list(env):
                    if key.endswith("API_KEY") and key != "DEEPSEEK_API_KEY":
                        del env[key]
                observations["no_worker_reply"] = worker_check(read_settings).model_dump(
                    mode="json"
                )
                assert observations["no_worker_reply"]["status"] == "unknown"
                worker_command = [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "app.workers.celery_app:celery_app",
                    "worker",
                    "--pool=solo",
                    "--loglevel=WARNING",
                    "--without-gossip",
                    "--without-mingle",
                    "--hostname=t17c-" + marker,
                ]
                wrong = start(
                    worker_command + ["--queues=wrong-" + marker],
                    root / "backend",
                    env,
                    "wrong-queue",
                )
                for _ in range(10):
                    observed = worker_check(read_settings)
                    if observed.facts.get("responders"):
                        break
                    time.sleep(0.25)
                observations["wrong_queue"] = observed.model_dump(mode="json")
                assert observed.status == "blocked"
                close(wrong)
                worker = start(
                    worker_command + ["--queues=" + own.executor_queue],
                    root / "backend",
                    env,
                    "worker",
                )
                for _ in range(10):
                    observed = worker_check(read_settings)
                    if observed.status == "ready":
                        break
                    time.sleep(0.25)
                assert observed.status == "ready", observed.model_dump()
                observations["healthy"] = asyncio.run(collect_readiness(read_settings)).model_dump(
                    mode="json"
                )
                # An additional existing native runtime, isolated port/directory, without modules.
                bare_dir = local / "redis-bare"
                bare_dir.mkdir()
                config = bare_dir / "memurai.conf"
                config.write_text(
                    'bind 127.0.0.1\nport 16381\nprotected-mode yes\nsave ""\nappendonly no\n'
                    + f'dir "{bare_dir.as_posix()}"\nrequirepass {password}\n',
                    encoding="utf-8",
                )
                bare = start(["D:/RedisSearch/memurai.exe", str(config)], root, env, "redis-bare")
                bare_settings = read_settings.model_copy(
                    update={"redis_port": 16381, "redis_password": SecretStr(password)}
                )
                bare_client = Redis.from_url(bare_settings.redis_url, socket_timeout=1)
                try:
                    for _ in range(40):
                        try:
                            if bare_client.ping():
                                break
                        except Exception:
                            time.sleep(0.1)
                    observations["actual_missing_modules"] = asyncio.run(
                        collect_readiness(bare_settings)
                    ).model_dump(mode="json")
                    checks = {c["id"]: c for c in observations["actual_missing_modules"]["checks"]}
                    assert (
                        checks["redis"]["status"] == "ready"
                        and checks["checkpoints"]["status"] == "blocked"
                    )
                finally:
                    bare_client.close()
                    close(bare)
                start(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "readiness_checks.review_api:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "18020",
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
                        "5187",
                        "--strictPort",
                    ],
                    root / "frontend",
                    env,
                    "ui",
                )
                with httpx.Client(base_url="http://127.0.0.1:18020", timeout=15) as api:
                    for _ in range(100):
                        try:
                            response = api.get("/api/v1/system/readiness")
                            if (
                                response.status_code == 200
                                and httpx.get("http://127.0.0.1:5187").status_code == 200
                            ):
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.25)
                    else:
                        raise RuntimeError("Owned readiness view did not start")
                    observations["http_readiness"] = response.json()["data"]
                    assert (
                        next(
                            c
                            for c in observations["http_readiness"]["checks"]
                            if c["id"] == "worker"
                        )["status"]
                        == "ready"
                    )
                    assert api.post("/api/v1/runs", json={}).status_code == 403
                assert sql_snapshot(engine) == snapshot
                assert ledgers(client) == before
                write(
                    dest / "observations.json",
                    {
                        "checked_at": datetime.now(UTC).isoformat(),
                        "provenance": (
                            "real MySQL and existing native Redis; "
                            "actual Celery solo inspect only, no task dispatch"
                        ),
                        "observations": observations,
                        "sql_before_after_identical": True,
                        "thirteen_ledgers_unchanged": True,
                        "new_model_calls": 0,
                        "bare_redis_stopped": True,
                        "linux_prefork_not_retested": True,
                    },
                )
                now = datetime.now(UTC)
                info = {
                    "marker": marker,
                    "url": "http://127.0.0.1:5187/environment",
                    "api": "http://127.0.0.1:18020",
                    "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=args.seconds)).isoformat(),
                    "stop_file": str(stop),
                    "read_only_sql_and_http": True,
                    "no_model_key_or_tasks": True,
                    "worker_scope": "owned readiness control channel only",
                    "new_model_calls": 0,
                }
                write(dest / "review-session.json", info)
                write(
                    root / ".local/t17c/latest-review.json",
                    {
                        **info,
                        "database": engine.url.database,
                        "evidence": dest.relative_to(root).as_posix(),
                        "pids": [p.pid for p in processes if p.poll() is None],
                    },
                )
                print(json.dumps(info), flush=True)
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline and not stop.exists():
                    assert worker.poll() is None and all(p.poll() is None for p in processes[-2:])
                    assert shutil.disk_usage("C:/").free > 2_000_000_000, (
                        "C-drive low space; no purge"
                    )
                    time.sleep(2)
            finally:
                for process in reversed(processes):
                    close(process)
                if created:
                    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                        c.execute(text(f"DROP USER '{user}'@:host"), {"host": "%"})
    finally:
        assert ledgers(client) == before
        # Cleanup receipts stay private; published observations remain immutable.
        write(
            local / "cleanup.json",
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "only_owned_resources_closed": True,
                "thirteen_ledgers_unchanged": True,
            },
        )
        client.close()
        admin.dispose()


if __name__ == "__main__":
    main()
