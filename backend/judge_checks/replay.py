"""Restore exactly the original sealed T17B artifacts for a free read-only view."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from redis import Redis
from sqlalchemy import DateTime, text
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.judging import evidence
from app.judging.service import JudgeService
from app.models import Artifact
from app.models.base import UTCDateTime
from judge_checks.lab import KEY, write

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from test_database import disposable_database, migrate  # noqa:E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--seconds", type=int, default=21600)
    args = parser.parse_args()
    root = PROJECT_ROOT
    source = (root / args.source).resolve()
    assert source.is_relative_to(root / "docs/evidence/T17B") and 1 <= args.seconds <= 86400
    for port in (18019, 5186):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    original = json.loads((source / "final-batch.json").read_bytes())
    batch = original["batch_id"]
    s = Settings()
    assert not any(
        getattr(s, k)
        for k in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    marker = uuid4().hex[:12]
    local = root / ".local/t17b" / ("replay-" + marker)
    local.mkdir(parents=True)
    dest = root / "docs/evidence/T17B" / ("replay-" + marker)
    dest.mkdir(parents=True)
    stop = local / "stop-review"
    admin = create_database_engine(s, database="mysql")
    client = Redis.from_url(s.redis_url)
    before = client.hgetall(KEY)
    archive_hash = hashlib.sha256((source / "raw-evidence.zip").read_bytes()).hexdigest()
    user, password = "ac_t17b_r_" + marker, uuid4().hex + uuid4().hex
    processes, created = [], False
    try:
        with disposable_database("T17B") as engine:
            migrate(engine)
            seed_database(engine, s.demo_seed_password.get_secret_value())
            with (
                zipfile.ZipFile(source / "raw-evidence.zip") as archive,
                Session(engine) as session,
                session.begin(),
            ):
                rows = json.loads(archive.read("sql-artifacts.json"))
                for row in rows:
                    assert row["run_id"] is None and row["owner_id"] == 1
                    assert row["metadata_json"]["generation_id"] == batch
                    path = (root / row["relative_path"]).resolve()
                    assert path.is_relative_to(evidence.ROOT.resolve())
                    body = archive.read(row["relative_path"])
                    assert hashlib.sha256(body).hexdigest() == row["sha256"]
                    assert len(body) == row["size_bytes"]
                    if path.exists():
                        assert path.read_bytes() == body
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(body)
                    for column in Artifact.__table__.columns:
                        if isinstance(column.type, (DateTime, UTCDateTime)) and row[column.name]:
                            row[column.name] = datetime.fromisoformat(row[column.name])
                    session.add(Artifact(**row))
            restored = JudgeService(engine, client, s).read(1, batch)
            for key in ("labels", "seal", "results", "reviews", "statistics"):
                assert restored[key] == original[key]
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
                env = {
                    **os.environ,
                    "PYTHONPATH": str(root / "backend"),
                    "MYSQL_DATABASE": engine.url.database,
                    "MYSQL_USER": user,
                    "MYSQL_PASSWORD": password,
                    "DEEPSEEK_API_KEY": "",
                    "ALLOW_PAID_MODEL_CALLS": "false",
                    "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                    "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
                    "JUDGE_ALLOW_PAID_MODEL_CALLS": "false",
                    "JUDGE_MAX_OUTPUT_TOKENS": "1536",
                    "AC_API_PROXY_TARGET": "http://127.0.0.1:18019",
                }
                for key in list(env):
                    if key.endswith("API_KEY") and key != "DEEPSEEK_API_KEY":
                        del env[key]
                commands = [
                    (
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "judge_checks.replay_api:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "18019",
                        ],
                        root / "backend",
                    ),
                    (
                        [
                            "D:/node.js/node.exe",
                            "node_modules/vite/bin/vite.js",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "5186",
                            "--strictPort",
                        ],
                        root / "frontend",
                    ),
                ]
                for i, (command, cwd) in enumerate(commands):
                    with (local / f"process-{i}.log").open("w", encoding="utf-8") as log:
                        processes.append(
                            subprocess.Popen(
                                command,
                                cwd=cwd,
                                env=env,
                                stdout=log,
                                stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            )
                        )
                with httpx.Client(
                    base_url="http://127.0.0.1:18019",
                    timeout=10,
                    auth=("demo_reviewer", s.demo_seed_password.get_secret_value()),
                ) as api:
                    for _ in range(100):
                        try:
                            response = api.get("/api/v1/judging/calibrations/" + batch)
                            if (
                                response.status_code == 200
                                and httpx.get("http://127.0.0.1:5186").status_code == 200
                            ):
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.25)
                    else:
                        raise RuntimeError("Replay services did not start")
                    assert response.json()["data"]["labels"] == original["labels"]
                    assert (
                        api.post(
                            "/api/v1/judging/calibrations/" + batch + "/seal", json={}
                        ).status_code
                        == 403
                    )
                now = datetime.now(UTC)
                info = {
                    "marker": marker,
                    "mode": "read_only_original_results",
                    "batch_id": batch,
                    "url": "http://127.0.0.1:5186/judging/" + batch,
                    "api": "http://127.0.0.1:18019",
                    "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=args.seconds)).isoformat(),
                    "stop_file": str(stop),
                    "source_archive_sha256": archive_hash,
                    "original_labels_results_statistics_identical": True,
                    "new_provider_calls": 0,
                    "no_model_key_worker_or_paid_switches": True,
                }
                write(
                    root / ".local/t17b/latest-review.json",
                    {**info, "database": engine.url.database, "pids": [p.pid for p in processes]},
                )
                write(dest / "review-session.json", info)
                print(json.dumps(info), flush=True)
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline and not stop.exists():
                    if any(p.poll() is not None for p in processes):
                        raise RuntimeError("Owned read-only view exited")
                    if shutil.disk_usage("C:/").free < 2_000_000_000:
                        raise RuntimeError("MySQL C-drive low space; no purge")
                    time.sleep(2)
            finally:
                for p in reversed(processes):
                    if p.poll() is None:
                        p.terminate()
                        p.wait(timeout=15)
                if created:
                    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                        c.execute(text(f"DROP USER '{user}'@:host"), {"host": "%"})
    finally:
        assert client.hgetall(KEY) == before
        assert (
            hashlib.sha256((source / "raw-evidence.zip").read_bytes()).hexdigest() == archive_hash
        )
        write(
            dest / "cleanup.json",
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "source_archive_and_ledger_unchanged": True,
                "only_owned_resources_closed": True,
            },
        )
        client.close()
        admin.dispose()


if __name__ == "__main__":
    main()
