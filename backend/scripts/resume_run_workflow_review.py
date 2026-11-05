"""Resume only the preserved T17H free-control lab; export before owned cleanup."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from redis import Redis
from sqlalchemy import text

from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import RunService
from operations.workers import Docker, load, start, stop
from workflow_checks.storage import export


def save(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=10800)
    args = parser.parse_args()
    assert 60 <= args.seconds <= 43200
    root = PROJECT_ROOT
    info = json.loads((root / ".local/t17h/latest-review.json").read_bytes())
    local, destination = Path(info["local"]), Path(info["evidence"])
    assert local.resolve().parent == (root / ".local/t17h").resolve()
    assert destination.resolve().parent == (root / "docs/evidence/T17H").resolve()
    assert info["profile"] == "t17h-" + info["marker"]
    previous = json.loads((local / "storage-cleanup.json").read_bytes())
    assert previous["removed"] is False and previous["owned_database"] == info["database"]
    assert not (destination / "resumed-final-export.zip").exists()
    assert not (local / "stop-review").exists()
    settings = Settings(_env_file=info["config_file"])
    base = Settings()
    assert settings.mysql_database == info["database"] != base.mysql_database
    assert settings.mysql_database.startswith("agentchaos_t17h_verify_")
    assert settings.mysql_user == "ac_t17h_" + info["marker"]
    assert not settings.deepseek_api_key.get_secret_value()
    assert not any(
        getattr(settings, name)
        for name in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    assert shutil.disk_usage("C:/").free > 3_000_000_000
    for port in (18022, 5189):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    engine = create_database_engine(settings)
    manifest = RunService(engine, settings).current_manifest()
    with engine.connect() as connection:
        stored = connection.scalar(
            text("SELECT config_hash FROM ac_target_versions WHERE id=:id"),
            {"id": info["version_id"]},
        )
    assert stored == manifest["config_hash"], "Preserved review source must still match"
    workers = root / ".local/workers" / info["profile"]
    docker, processes = Docker(), []
    values = settings.model_dump(mode="json")
    for key in ("mysql_password", "redis_password", "deepseek_api_key", "demo_seed_password"):
        values[key] = getattr(settings, key).get_secret_value()
    environment = {
        **os.environ,
        **{k.upper(): str(v).lower() if isinstance(v, bool) else str(v) for k, v in values.items()},
        "PYTHONPATH": str(root / "backend"),
        "AC_API_PROXY_TARGET": "http://127.0.0.1:18022",
    }

    def close():
        if load(workers).get("containers"):
            stop(workers, docker)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)

    try:
        start(
            workers,
            info["profile"],
            Path(info["config_file"]),
            "agentchaos-t07:dev",
            docker,
            control=True,
        )
        commands = (
            (
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
                "resumed-api",
            ),
            (
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
                "resumed-ui",
            ),
        )
        for command, directory, label in commands:
            with (local / (label + ".log")).open("w", encoding="utf-8") as log:
                processes.append(
                    subprocess.Popen(
                        command,
                        cwd=directory,
                        env=environment,
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                )
        now = datetime.now(UTC)
        info.update(
            resumed_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=args.seconds)).isoformat(),
            api_launcher_pid=processes[0].pid,
            ui_pid=processes[1].pid,
        )
        save(root / ".local/t17h/latest-review.json", info)
        save(
            destination / "review-resumed-session.json",
            {
                k: v
                for k, v in info.items()
                if k not in {"config_file", "database", "local", "worker_state"}
            },
        )
        print(
            json.dumps({"resumed": True, "profile": info["profile"], "url": info["url"]}),
            flush=True,
        )
        until = time.monotonic() + args.seconds
        while time.monotonic() < until and not Path(info["stop_file"]).exists():
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("Owned review process exited; keep database and logs")
            if shutil.disk_usage("C:/").free < 2_000_000_000:
                raise RuntimeError("Low C space; keep owned database, no automatic purge")
            time.sleep(1)
        close()
        result = export(engine, destination / "resumed-final-export.zip", files=workers / "data")
        save(destination / "resumed-final-export.json", result)
        assert result["verified"]
        engine.dispose()
        admin = create_database_engine(base, database="mysql")
        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text(f"DROP DATABASE `{settings.mysql_database}`"))
                connection.execute(text(f"DROP USER IF EXISTS '{settings.mysql_user}'@'%'"))
        finally:
            admin.dispose()
        with Redis.from_url(settings.redis_url) as client:
            keys = list(client.scan_iter(settings.executor_namespace + ":*"))
            if keys:
                client.delete(*keys)
        save(
            local / "resumed-storage-cleanup.json",
            {"owned_database": info["database"], "export_verified": True, "removed": True},
        )
        print(json.dumps({"export_verified": True, "owned_database_removed": True}), flush=True)
    finally:
        close()
        engine.dispose()


if __name__ == "__main__":
    main()
