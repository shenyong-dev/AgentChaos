"""T11 real generator HTTP + disposable SQL/Redis, explicit offline controls and review.

No Agent/judge calls. This harness never invents human approval. Review decisions
must come from a human in the UI or a separately recorded direct user confirmation.
"""

import argparse
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
from langchain_core.messages import AIMessage
from redis import Redis
from sqlalchemy import DateTime, func, select, text
from sqlalchemy.orm import Session

from app.cases import evidence as generation_evidence
from app.cases.budget import AUTHORIZATION
from app.cases.service import CaseService
from app.core.config import PROJECT_ROOT, Settings
from app.core.passwords import hash_password
from app.db.engine import create_database_engine
from app.db.seeds import seed_database
from app.evidence.recorder import redact
from app.models import Artifact, TestCase, User
from app.models.base import UTCDateTime
from app.schemas.cases import GenerateCandidates

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from agent_test_support import OfflineGateway  # noqa: E402
from casegen_test_support import proposal  # noqa: E402
from test_database import disposable_database, migrate  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--real-model", action="store_true")
    modes.add_argument("--offline", action="store_true")
    modes.add_argument(
        "--replay",
        type=Path,
        help="Restore immutable exported real generation and human decisions; no automatic calls",
    )
    parser.add_argument(
        "--enable-generation",
        action="store_true",
        help="Allow manual T11 generation in a replay lab using the existing ledger",
    )
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--review-seconds", type=int, default=7200)
    parser.add_argument("--port", type=int, default=18012)
    parser.add_argument("--ui-port", type=int, default=5179)
    args = parser.parse_args()
    assert 1 <= args.review_seconds <= 86400
    assert args.port != args.ui_port and all(1024 <= p <= 65535 for p in (args.port, args.ui_port))
    for port in (args.port, args.ui_port):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} occupied; nothing changed") from None
    root, base = PROJECT_ROOT, Settings()
    assert not base.allow_paid_model_calls and not base.executor_allow_paid_model_calls
    assert not base.casegen_allow_paid_model_calls
    r = Redis.from_url(base.redis_url, socket_timeout=15)
    key = f"ac:t11:budget:{AUTHORIZATION}:case_generator"

    def ledger(which):
        return {a.decode(): int(b) for a, b in r.hgetall(which).items()}

    paid_enabled = args.real_model or args.enable_generation
    assert not args.enable_generation or args.replay
    if paid_enabled:
        assert (root / ".local/t11" / f"{AUTHORIZATION}.initialized.json").exists()
        assert ledger(key).get("limit") == -1
    previous = json.loads((root / "docs/evidence/T11/preparation.json").read_bytes())["old_ledgers"]
    assert {k: ledger(k) for k in previous} == previous
    marker = uuid4().hex[:12]
    dest = (
        root
        / "docs/evidence/T11"
        / (("replay-" if args.replay else "real-" if args.real_model else "offline-") + marker)
    )
    local = root / ".local/t11" / marker
    dest.mkdir(parents=True)
    local.mkdir(parents=True)

    def save(name, value):
        (dest / name).write_text(
            json.dumps(redact(value, base), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    password = base.demo_seed_password.get_secret_value()
    assert password
    sql_user, sql_password = "ac_t11_" + marker, secrets.token_hex(24)
    admin = create_database_engine(base, database="mysql")
    processes, created_user, batches = [], False, {}
    report = {
        "mode": "replay_original_real_generation_and_confirmed_human_decisions_zero_new_calls"
        if args.replay
        else "real_case_generator_HTTP"
        if args.real_model
        else "explicit_offline_controls_real_SQL_Redis",
        "ledger_before": ledger(key),
        "batches": {},
        "human_review": "pending",
    }
    with disposable_database("T11") as engine:
        migrate(engine)
        seed_database(engine, password)
        with Session(engine) as session, session.begin():
            session.add(
                User(
                    username="t11_other",
                    role="operator",
                    enabled=True,
                    password_hash=hash_password(password),
                )
            )
            sentinel = "T11-HOLDOUT-CONTROL-" + uuid4().hex
            session.add(
                TestCase(
                    case_key="T11-HOLDOUT-SENTINEL-" + marker.upper(),
                    revision=1,
                    family_key="T11-holdout-control-" + marker,
                    category="security",
                    dataset_split="holdout",
                    source="controlled_leak_sentinel_not_formal_dataset",
                    turns_json=[{"user": sentinel}],
                    fixture_json={"private_control": sentinel},
                    expected_json={"hidden_answer": sentinel},
                    content_hash="0" * 64,
                    review_status="draft",
                )
            )

        if args.replay:
            import hashlib

            restore = json.loads(args.replay.read_bytes())
            source = (root / restore["source_evidence"]).resolve()
            assert source.is_relative_to(root / "docs/evidence/T11")
            batches.update(restore["batches"])
            replay_root = generation_evidence.ROOT / "replay" / marker
            replay_root.mkdir(parents=True)
            with Session(engine) as session, session.begin():
                for model, values in (
                    (Artifact, restore["artifacts"]),
                    (TestCase, restore["cases"]),
                ):
                    for original in values:
                        value = dict(original)
                        assert set(value) == {c.name for c in model.__table__.columns}
                        for column in model.__table__.columns:
                            if (
                                isinstance(column.type, (DateTime, UTCDateTime))
                                and value[column.name]
                            ):
                                value[column.name] = datetime.fromisoformat(value[column.name])
                        if model is Artifact:
                            assert value["run_id"] is None and value["owner_id"] == 1
                            assert value["kind"] in {
                                "casegen_request",
                                "casegen_call",
                                "casegen_result",
                                "case_review",
                            }
                            raw = source / "artifacts" / Path(value["relative_path"]).name
                            assert hashlib.sha256(raw.read_bytes()).hexdigest() == value["sha256"]
                            target = replay_root / raw.name
                            shutil.copyfile(raw, target)
                            value["relative_path"] = target.relative_to(root).as_posix()
                        else:
                            assert value["dataset_split"] == "dev" and value["source"].startswith(
                                "case_generator:T11:"
                            )
                        session.add(model(**value))
            report["replayed_from"] = restore["source_evidence"]
            report["restore_file_hash"] = hashlib.sha256(args.replay.read_bytes()).hexdigest()
            report["human_review"] = "7 direct user-confirmed decisions restored unchanged"

        def export():
            service = CaseService(engine, r, base)
            for label, batch in batches.items():
                save(label + "-batch.json", service.read(1, batch))
            with Session(engine) as session:
                for artifact in session.scalars(select(Artifact).where(Artifact.run_id.is_(None))):
                    path = root / artifact.relative_path
                    assert (
                        __import__("hashlib").sha256(path.read_bytes()).hexdigest()
                        == artifact.sha256
                    )
                    target = dest / "artifacts" / Path(artifact.relative_path).name
                    target.parent.mkdir(exist_ok=True)
                    if not target.exists():
                        shutil.copyfile(path, target)
                cases = [
                    {
                        "id": c.id,
                        "case_key": c.case_key,
                        "revision": c.revision,
                        "review_status": c.review_status,
                        "family_key": c.family_key,
                        "dataset_split": c.dataset_split,
                        "turns": c.turns_json,
                        "expected": c.expected_json,
                        "fixture": c.fixture_json,
                        "content_hash": c.content_hash,
                        "reviewer_id": c.reviewer_id,
                    }
                    for c in session.scalars(
                        select(TestCase)
                        .where(TestCase.dataset_split == "dev")
                        .order_by(TestCase.id)
                    )
                ]
            save("case-revisions.json", cases)
            for batch in batches.values():
                source = (
                    (root / restore["source_evidence"] / "model-calls" / batch)
                    if args.replay
                    else generation_evidence.ROOT / "provider" / batch
                )
                if source.exists():
                    shutil.copytree(source, dest / "model-calls" / batch, dirs_exist_ok=True)
            report["ledger_after"] = ledger(key)
            report["old_ledgers_unchanged"] = {k: ledger(k) for k in previous} == previous
            assert report["old_ledgers_unchanged"]
            report["batches"] = {
                label: {
                    "generation_id": batch,
                    "url": f"http://127.0.0.1:{args.ui_port}/cases/{batch}",
                }
                for label, batch in batches.items()
            }
            save("matrix.json", report)

        def create_control(output):
            bad = proposal("capacity", day="2026-11-02", wrong_count=True)
            gateway = OfflineGateway()
            gateway.queue.append(
                AIMessage(content=json.dumps({"candidates": [bad]}, ensure_ascii=False))
            )
            controls = CaseService(
                engine,
                r,
                base,
                gateway_factory=lambda _: gateway,
                clock=lambda: datetime(2026, 11, 1, 1, tzinfo=UTC),
            )
            value = controls.generate(
                1, GenerateCandidates(request_id=uuid4(), profiles=["capacity"])
            )
            output["batch"] = value["generation_id"]
            output["boundary"] = (
                "Authored wrong-count fixture with offline model double; "
                "not a natural DeepSeek defect or quality sample."
            )

        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(
                    text(f"CREATE USER '{sql_user}'@:host IDENTIFIED BY :password"),
                    {"host": "%", "password": sql_password},
                )
                created_user = True
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
                "ALLOW_PAID_MODEL_CALLS": "false",
                "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                "CASEGEN_ALLOW_PAID_MODEL_CALLS": "true" if paid_enabled else "false",
                "DEEPSEEK_API_KEY": base.deepseek_api_key.get_secret_value()
                if paid_enabled
                else "",
                "PYTHONPATH": str(root / "backend"),
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
                raise RuntimeError("Owned servers failed to start")
            with httpx.Client(
                base_url=f"http://127.0.0.1:{args.port}",
                auth=("demo_operator", password),
                timeout=100,
            ) as client:
                for label in [] if args.replay else ["initial", "duplicate"]:
                    body = GenerateCandidates(request_id=uuid4())
                    if args.real_model:
                        response = client.post(
                            "/api/v1/case-generation", json=body.model_dump(mode="json")
                        )
                        assert response.status_code == 201
                        value = response.json()["data"]
                        # Exact request replay must be a read of saved evidence.
                        replay = client.post(
                            "/api/v1/case-generation", json=body.model_dump(mode="json")
                        )
                        assert replay.status_code == 201 and replay.json()["data"] == value
                    else:
                        gateway = OfflineGateway()
                        gateway.queue.append(
                            AIMessage(
                                content=json.dumps(
                                    {"candidates": [proposal(p) for p in body.profiles]},
                                    ensure_ascii=False,
                                )
                            )
                        )
                        service = CaseService(
                            engine,
                            r,
                            base,
                            gateway_factory=lambda _, g=gateway: g,
                            clock=lambda: datetime(2026, 10, 28, 1, tzinfo=UTC),
                        )
                        value = service.generate(1, body)
                    batches[label] = value["generation_id"]
                    report[label + "_initial_metrics"] = value["metrics"]
                    export()
                    print(
                        json.dumps(
                            {"batch": label, "status": value["status"], "metrics": value["metrics"]}
                        ),
                        flush=True,
                    )
                # Explicitly authored wrong expected, separate from model-quality metrics.
                if args.replay:
                    export()
                    assert ledger(key) == report["ledger_before"]
                    print(json.dumps({"replayed": True, "provider_calls_added": 0}), flush=True)
                else:
                    create_control(controls_output := {})
                    batches["wrong_expected_control"] = controls_output["batch"]
                    report["control_boundary"] = controls_output["boundary"]
                    export()

            provider_records = list((dest / "model-calls").rglob("*.json"))
            assert all(sentinel not in p.read_text(encoding="utf-8") for p in provider_records)
            report["holdout_sentinel_not_in_provider_requests"] = True
            report["sentinel_scope"] = "Controlled private row, not formal T13 dataset."
            export()
            ready = {
                "evidence": dest.relative_to(root).as_posix(),
                "database": engine.url.database,
                "sql_user": sql_user,
                "ready_at": datetime.now(UTC).isoformat(),
                "expires_approximately": (
                    datetime.now(UTC) + timedelta(seconds=args.review_seconds)
                ).isoformat(),
                "stop_file": str(local / "stop-review"),
                "casegen_enabled": paid_enabled,
                "agent_enabled": False,
                "judge_enabled": False,
                "batches": report["batches"],
            }
            (local.parent / "latest-review.json").write_text(
                json.dumps(ready, indent=2) + "\n", encoding="utf-8"
            )
            save("review-session.json", ready)
            print(
                json.dumps(
                    {
                        "review_ready": True,
                        "evidence": ready["evidence"],
                        "batches": report["batches"],
                    }
                ),
                flush=True,
            )
            if args.review:
                until = time.monotonic() + args.review_seconds
                last = -1
                while time.monotonic() < until and not (local / "stop-review").exists():
                    with Session(engine) as session:
                        count = session.scalar(select(func.count()).select_from(Artifact))
                    if count != last:
                        export()
                        last = count
                    time.sleep(2)
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
            if created_user:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    connection.execute(
                        text(f"DROP USER IF EXISTS '{sql_user}'@:host"), {"host": "%"}
                    )
            save(
                "cleanup.json",
                {
                    "servers_stopped": True,
                    "sql_user_removed": True,
                    "database_removed_on_context_exit": True,
                    "ledger": ledger(key),
                    "old_ledgers_unchanged": {k: ledger(k) for k in previous} == previous,
                },
            )
    admin.dispose()
    r.close()


if __name__ == "__main__":
    main()
