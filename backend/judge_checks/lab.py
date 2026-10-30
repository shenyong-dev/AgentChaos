"""Six-hour owned human-label view. Judge calls require an explicit local signal and seal."""

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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.core.passwords import hash_password
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.judging import evidence
from app.judging.budget import PROTOCOL_AUTHORIZATION
from app.judging.engine import manifest
from app.judging.service import JudgeService
from app.models import Artifact, User
from app.schemas.judging import RequestID

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from test_database import disposable_database, migrate  # noqa:E402

ROOT = PROJECT_ROOT / "docs/evidence/T17B"
KEY = f"ac:t12:budget:{PROTOCOL_AUTHORIZATION}:judge"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def authorize(client):
    approved = json.loads((PROJECT_ROOT / "docs/fixtures/T17B/authorization.json").read_bytes())
    assert approved["authorization"] == PROTOCOL_AUTHORIZATION and approved["approved"] is True
    marker = PROJECT_ROOT / ".local/t17b" / (PROTOCOL_AUTHORIZATION + ".initialized.json")
    if marker.exists() and not client.exists(KEY):
        raise RuntimeError("Previously initialized ledger missing; never recreate allowance")
    created = client.eval(
        "if redis.call('EXISTS',KEYS[1])==1 then return 0 end "
        "redis.call('HSET',KEYS[1],'limit',500000) return 1",
        1,
        KEY,
    )
    snapshot = {k.decode(): int(v) for k, v in client.hgetall(KEY).items()}
    assert snapshot["limit"] == 500_000
    if not marker.exists():
        write(
            marker,
            {
                "initialized_at": datetime.now(UTC).isoformat(),
                "authorization": approved,
                "key": KEY,
                "initial": snapshot,
            },
        )
    return bool(created)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=21600)
    args = parser.parse_args()
    assert 1 <= args.seconds <= 86400
    for port in (18019, 5186):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    settings = Settings()
    assert not any(
        getattr(settings, name)
        for name in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    client = Redis.from_url(settings.redis_url, socket_timeout=15)
    baseline = json.loads((ROOT / "preparation.json").read_bytes())

    def ledgers():
        return {
            key: {k.decode(): int(v) for k, v in client.hgetall(key).items()}
            for key in baseline["ledgers"]
        }

    assert ledgers() == baseline["ledgers"]
    authorize(client)
    settings = settings.model_copy(
        update={
            "judge_budget_authorization_id": PROTOCOL_AUTHORIZATION,
            "judge_budget_usd": 0.5,
            "judge_budget_unlimited": False,
            "judge_max_output_tokens": 1536,
        }
    )
    marker = uuid4().hex[:12]
    local = PROJECT_ROOT / ".local/t17b" / marker
    destination = ROOT / ("calibration-" + marker)
    local.mkdir(parents=True)
    destination.mkdir(parents=True)
    stop, signal = local / "stop-review", local / "run-judge"
    user, sql_password = "ac_t17b_" + marker, uuid4().hex + uuid4().hex
    password = settings.demo_seed_password.get_secret_value()
    admin = create_database_engine(settings, database="mysql")
    processes, created_user, batch = [], False, None
    try:
        with disposable_database("T17B") as engine:
            migrate(engine)
            seed_database(engine, password)
            with Session(engine) as session, session.begin():
                session.add(
                    User(
                        username="t17b_other",
                        role="operator",
                        enabled=True,
                        password_hash=hash_password(password),
                    )
                )
            svc = JudgeService(engine, client, settings, dataset_task="T17B")
            batch = str(uuid4())
            current = svc.create(1, RequestID(request_id=batch))
            assert not current["labels"] and not current["results"]
            write(destination / "protocol.json", current["frozen"]["manifest"])
            write(
                destination / "dataset-reference.json",
                {
                    "path": "docs/fixtures/T17B/samples.json",
                    "dataset_hash": current["frozen"]["dataset"]["dataset_hash"],
                    "batch_id": batch,
                },
            )
            # Capture actual source before human input and provider use; no secrets or dependencies.
            with zipfile.ZipFile(destination / "source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
                source_paths = [
                    *sorted((PROJECT_ROOT / "backend/app").rglob("*.py")),
                    PROJECT_ROOT / "backend/app/judging/prompt.txt",
                    PROJECT_ROOT / "backend/judge_checks/lab.py",
                    PROJECT_ROOT / "backend/judge_checks/review_api.py",
                ]
                for path in source_paths:
                    archive.write(path, path.relative_to(PROJECT_ROOT).as_posix())
            try:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                    c.execute(
                        text(f"CREATE USER '{user}'@:host IDENTIFIED BY :password"),
                        {"host": "%", "password": sql_password},
                    )
                    created_user = True
                    c.execute(
                        text(
                            "GRANT SELECT,INSERT,UPDATE,DELETE,EXECUTE "
                            f"ON `{engine.url.database}`.* TO '{user}'@:host"
                        ),
                        {"host": "%"},
                    )
                env = {
                    **os.environ,
                    "PYTHONPATH": str(PROJECT_ROOT / "backend"),
                    "MYSQL_DATABASE": engine.url.database,
                    "MYSQL_USER": user,
                    "MYSQL_PASSWORD": sql_password,
                    "DEEPSEEK_API_KEY": "",
                    "ALLOW_PAID_MODEL_CALLS": "false",
                    "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                    "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
                    "JUDGE_ALLOW_PAID_MODEL_CALLS": "false",
                    "JUDGE_BUDGET_AUTHORIZATION_ID": PROTOCOL_AUTHORIZATION,
                    "JUDGE_BUDGET_USD": "0.5",
                    "JUDGE_BUDGET_UNLIMITED": "false",
                    "JUDGE_MAX_OUTPUT_TOKENS": "1536",
                    "AC_T17B_BATCH": batch,
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
                            "judge_checks.review_api:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "18019",
                        ],
                        PROJECT_ROOT / "backend",
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
                        PROJECT_ROOT / "frontend",
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
                    base_url="http://127.0.0.1:18019", timeout=10, auth=("demo_reviewer", password)
                ) as api:
                    for _ in range(100):
                        try:
                            r = api.get("/api/v1/judging/calibrations/" + batch)
                            if (
                                r.status_code == 200
                                and httpx.get("http://127.0.0.1:5186").status_code == 200
                            ):
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.25)
                    else:
                        raise RuntimeError("Owned review services did not start")
                    assert r.json()["data"]["frozen"]["manifest"] == manifest(settings)
                    assert api.post("/api/v1/judging/calibrations", json={}).status_code == 403
                    assert (
                        api.get("/api/v1/judging/access").json()["data"]["judge_enabled"] is False
                    )
                now = datetime.now(UTC)
                info = {
                    "marker": marker,
                    "batch_id": batch,
                    "api": "http://127.0.0.1:18019",
                    "url": "http://127.0.0.1:5186/judging/" + batch,
                    "stop_file": str(stop),
                    "judge_signal": str(signal),
                    "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=args.seconds)).isoformat(),
                    "database": engine.url.database,
                    "pids": [p.pid for p in processes],
                    "human_labels": 0,
                    "judge_calls": 0,
                    "http_has_no_model_key": True,
                }
                write(local / "connection.json", {"database": engine.url.database})
                write(PROJECT_ROOT / ".local/t17b/latest-review.json", info)
                write(
                    destination / "review-session.json",
                    {k: v for k, v in info.items() if k not in ("database", "pids")},
                )
                print(json.dumps(info), flush=True)
                finish, called, sealed = time.monotonic() + args.seconds, False, False
                while time.monotonic() < finish and not stop.exists():
                    if any(p.poll() is not None for p in processes):
                        raise RuntimeError("Owned view exited unexpectedly")
                    if shutil.disk_usage("C:/").free < 2_000_000_000:
                        raise RuntimeError("MySQL C-drive free space below guard; no purge")
                    current = svc.read(1, batch)
                    if current["seal"] and not sealed:
                        write(destination / "independent-seal.json", current["seal"])
                        sealed = True
                        print(
                            json.dumps(
                                {"sealed": batch, "labels_hash": current["seal"]["labels_hash"]}
                            ),
                            flush=True,
                        )
                    if signal.exists() and not called:
                        if not sealed:
                            raise RuntimeError("Judge signal before independent human seal")
                        called = True
                        paid = settings.model_copy(update={"judge_allow_paid_model_calls": True})
                        judge = JudgeService(engine, client, paid, dataset_task="T17B")
                        for sample in current["frozen"]["dataset"]["samples"]:
                            assert ledgers() == baseline["ledgers"]
                            result = judge.evaluate(
                                1, batch, sample["id"], RequestID(request_id=uuid4())
                            )
                            print(
                                json.dumps({"sample": sample["id"], "status": result["status"]}),
                                flush=True,
                            )
                        write(destination / "final-batch.json", judge.read(1, batch))
                        export(engine, destination, settings, client, batch)
                        print(json.dumps({"completed": batch}), flush=True)
                    time.sleep(2)
            finally:
                for p in reversed(processes):
                    if p.poll() is None:
                        p.terminate()
                        p.wait(timeout=15)
                if batch:
                    export(engine, destination, settings, client, batch)
                if created_user:
                    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                        c.execute(text(f"DROP USER '{user}'@:host"), {"host": "%"})
    finally:
        write(
            destination / "cleanup.json",
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "old_ledgers_unchanged": ledgers() == baseline["ledgers"],
                "budget_preserved": True,
                "only_owned_database_and_user": True,
            },
        )
        client.close()
        admin.dispose()


def export(engine, destination, settings, client, batch):
    rows, members = [], {}
    with Session(engine) as session:
        for row in session.scalars(select(Artifact).order_by(Artifact.id)):
            evidence.read(row)
            rows.append({c.name: getattr(row, c.name) for c in Artifact.__table__.columns})
            members[row.relative_path] = (PROJECT_ROOT / row.relative_path).read_bytes()
    provider = evidence.ROOT / "provider" / batch
    if provider.exists():
        for path in sorted(provider.glob("*.json")):
            members[path.relative_to(PROJECT_ROOT).as_posix()] = path.read_bytes()
    members["sql-artifacts.json"] = (
        json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n"
    ).encode()
    with zipfile.ZipFile(destination / "raw-evidence.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    write(
        destination / "archive-manifest.json",
        {
            name: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
            for name, body in members.items()
        },
    )
    final = JudgeService(engine, client, settings).read(1, batch)
    write(destination / "latest-batch.json", final)
    write(destination / "ledger.json", {k.decode(): int(v) for k, v in client.hgetall(KEY).items()})
    assert canonical_hash(final["frozen"]["manifest"]) == canonical_hash(manifest(settings))


if __name__ == "__main__":
    main()
