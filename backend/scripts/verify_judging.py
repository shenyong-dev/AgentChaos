"""Owned T12 calibration lab. Never fabricates labels or automatically calls a model."""

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
from sqlalchemy import DateTime, func, select, text
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, Settings
from app.core.passwords import hash_password
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evidence.recorder import redact
from app.judging import evidence
from app.judging.budget import AUTHORIZATION
from app.judging.service import JudgeService
from app.models import Artifact, User
from app.models.base import UTCDateTime

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from test_database import disposable_database, migrate  # noqa:E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=int, default=14400)
    p.add_argument("--port", type=int, default=18013)
    p.add_argument("--ui-port", type=int, default=5180)
    p.add_argument(
        "--replay", type=Path, help="Restore original labels/calls/reviews; no paid calls"
    )
    args = p.parse_args()
    assert 1 <= args.seconds <= 86400
    for port in (args.port, args.ui_port):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    root = PROJECT_ROOT
    s = Settings()
    r = Redis.from_url(s.redis_url, socket_timeout=15)
    assert not any(
        (
            s.allow_paid_model_calls,
            s.executor_allow_paid_model_calls,
            s.casegen_allow_paid_model_calls,
            s.judge_allow_paid_model_calls,
        )
    )
    key = f"ac:t12:budget:{AUTHORIZATION}:judge"

    def ledger(k):
        return {a.decode(): int(b) for a, b in r.hgetall(k).items()}

    old = json.loads((root / "docs/evidence/T12/preparation.json").read_bytes())["old_ledgers"]
    assert {k: ledger(k) for k in old} == old
    assert ledger(key).get("limit") == -1
    marker = uuid4().hex[:12]
    local = root / ".local/t12" / marker
    dest = root / "docs/evidence/T12" / (("replay-" if args.replay else "lab-") + marker)
    local.mkdir(parents=True)
    dest.mkdir(parents=True)

    def save(name, value):
        (dest / name).write_text(
            json.dumps(redact(value, s), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    password = s.demo_seed_password.get_secret_value()
    sql_user = "ac_t12_" + marker
    sql_password = secrets.token_hex(24)
    admin = create_database_engine(s, database="mysql")
    processes = []
    created_user = False
    with disposable_database("T12") as engine:
        migrate(engine)
        seed_database(engine, password)
        with Session(engine) as session, session.begin():
            session.add(
                User(
                    username="t12_other",
                    role="operator",
                    enabled=True,
                    password_hash=hash_password(password),
                )
            )
        restore = None
        if args.replay:
            restore = json.loads(args.replay.read_bytes())
            source = (root / restore["source_evidence"]).resolve()
            assert source.is_relative_to(root / "docs/evidence/T12")
            restored_files = evidence.ROOT / "replay" / marker
            restored_files.mkdir(parents=True)
            with Session(engine) as session, session.begin():
                for original in restore["artifacts"]:
                    value = dict(original)
                    assert set(value) == {c.name for c in Artifact.__table__.columns}
                    assert value["run_id"] is None and value["owner_id"] == 1
                    assert value["kind"] in {
                        "judge_request",
                        "judge_call",
                        "judge_result",
                        "judge_label",
                        "judge_review",
                    }
                    assert value["metadata_json"]["generation_id"] == restore["batch_id"]
                    for column in Artifact.__table__.columns:
                        if isinstance(column.type, (DateTime, UTCDateTime)) and value[column.name]:
                            value[column.name] = datetime.fromisoformat(value[column.name])
                    raw = source / "artifacts" / Path(value["relative_path"]).name
                    assert hashlib.sha256(raw.read_bytes()).hexdigest() == value["sha256"]
                    assert raw.stat().st_size == value["size_bytes"]
                    target = restored_files / raw.name
                    shutil.copyfile(raw, target)
                    value["relative_path"] = target.relative_to(root).as_posix()
                    session.add(Artifact(**value))
        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(
                    text(f"CREATE USER '{sql_user}'@:host IDENTIFIED BY :password"),
                    {"host": "%", "password": sql_password},
                )
                created_user = True
                c.execute(
                    text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE "
                        + f"ON `{engine.url.database}`.* TO '{sql_user}'@:host"
                    ),
                    {"host": "%"},
                )
            env = {
                **os.environ,
                "PYTHONPATH": str(root / "backend"),
                "MYSQL_DATABASE": engine.url.database,
                "MYSQL_USER": sql_user,
                "MYSQL_PASSWORD": sql_password,
                "ALLOW_PAID_MODEL_CALLS": "false",
                "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
                "JUDGE_ALLOW_PAID_MODEL_CALLS": "false" if args.replay else "true",
                "AC_API_PROXY_TARGET": f"http://127.0.0.1:{args.port}",
            }
            if args.replay:
                env["DEEPSEEK_API_KEY"] = ""

            def spawn(cmd, cwd, name):
                with (local / (name + ".log")).open("w", encoding="utf-8") as out:
                    process = subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        env=env,
                        stdout=out,
                        stderr=out,
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
                    "--reload",
                    "--reload-dir",
                    str(root / "backend/app"),
                ],
                root / "backend",
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
                root / "frontend",
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
                time.sleep(0.3)
            else:
                raise RuntimeError("Owned lab servers did not start")
            with httpx.Client(
                base_url=f"http://127.0.0.1:{args.port}",
                auth=("demo_operator", password),
                timeout=20,
            ) as c:
                if restore:
                    batch = restore["batch_id"]
                    response = c.get("/api/v1/judging/calibrations/" + batch)
                    response.raise_for_status()
                    value = response.json()["data"]
                    assert len(value["labels"]) == 30 and len(value["results"]) == 30
                    assert not value["pending_reviews"]
                    original = json.loads((source / "final-batch-api.json").read_bytes())
                    for field in ("labels", "seal", "results", "reviews", "statistics"):
                        assert value[field] == original[field]
                else:
                    response = c.post(
                        "/api/v1/judging/calibrations", json={"request_id": str(uuid4())}
                    )
                    assert response.status_code == 201, (response.status_code, response.text)
                    value = response.json()["data"]
                    batch = value["batch_id"]
                    assert not value["labels"] and not value["results"]
            ready = {
                "database": engine.url.database,
                "sql_user": sql_user,
                "batch_id": batch,
                "evidence": dest.relative_to(root).as_posix(),
                "url": f"http://127.0.0.1:{args.ui_port}/judging/{batch}",
                "api": f"http://127.0.0.1:{args.port}",
                "ready_at": datetime.now(UTC).isoformat(),
                "expires_approximately": (
                    datetime.now(UTC) + timedelta(seconds=args.seconds)
                ).isoformat(),
                "stop_file": str(local / "stop-review"),
                "judge_enabled": not bool(args.replay),
                "agent_enabled": False,
                "case_generator_enabled": False,
                "boundary": (
                    "Original human labels, real blind calls and confirmed reviews restored; "
                    "zero new calls"
                )
                if args.replay
                else (
                    "Thirty real-human labels required before any blind judge call. "
                    "No fabricated annotations."
                ),
            }
            (local.parent / "latest-review.json").write_text(
                json.dumps(ready, indent=2) + "\n", encoding="utf-8"
            )
            save("review-session.json", ready)

            def export():
                value = JudgeService(engine, r, s).read(1, batch)
                save("batch.json", value)
                with Session(engine) as session:
                    artifacts = list(session.scalars(select(Artifact).order_by(Artifact.id)))
                    for a in artifacts:
                        path = root / a.relative_path
                        assert hashlib.sha256(path.read_bytes()).hexdigest() == a.sha256
                        target = dest / "artifacts" / path.name
                        target.parent.mkdir(exist_ok=True)
                        if not target.exists():
                            shutil.copyfile(path, target)
                    save(
                        "replay-records.json",
                        {
                            "source_evidence": dest.relative_to(root).as_posix(),
                            "batch_id": batch,
                            "artifacts": [
                                {c.name: getattr(a, c.name) for c in Artifact.__table__.columns}
                                for a in artifacts
                            ],
                        },
                    )
                provider = source / "model-calls" if restore else evidence.ROOT / "provider" / batch
                if provider.exists():
                    shutil.copytree(provider, dest / "model-calls", dirs_exist_ok=True)
                assert {k: ledger(k) for k in old} == old
                save(
                    "matrix.json",
                    {
                        "labels": len(value["labels"]),
                        "sealed": bool(value["seal"]),
                        "results": len(value["results"]),
                        "statistics": value["statistics"],
                        "pending_reviews": value["pending_reviews"],
                        "ledger": ledger(key),
                        "old_ledgers_unchanged": True,
                    },
                )

            export()
            print(json.dumps({"review_ready": True, **ready}), flush=True)
            until = time.monotonic() + args.seconds
            last = -1
            while time.monotonic() < until and not (local / "stop-review").exists():
                with Session(engine) as session:
                    count = session.scalar(select(func.count()).select_from(Artifact))
                if count != last:
                    export()
                    last = count
                time.sleep(3)
            export()
        except BaseException as exc:
            save("failed-report.json", {"error_type": type(exc).__name__})
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
            if created_user:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                    c.execute(text(f"DROP USER IF EXISTS '{sql_user}'@:host"), {"host": "%"})
            save(
                "cleanup.json",
                {
                    "servers_stopped": True,
                    "sql_user_removed": True,
                    "database_removed_on_context_exit": True,
                    "ledger": ledger(key),
                    "old_ledgers_unchanged": {k: ledger(k) for k in old} == old,
                },
            )
    admin.dispose()
    r.close()


if __name__ == "__main__":
    main()
