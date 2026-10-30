"""Eight real-model development scenarios with production runner and separate ledger."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.evidence.reader import TraceReader
from app.evidence.recorder import redact
from app.executors.budget import FIELD_SOURCE_AUTHORIZATION
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content
from app.models import Artifact, Booking, BookingDraft, TestCase, User
from app.schemas.runs import CreateRun

sys.path.insert(0, str(PROJECT_ROOT / "backend/tests"))
from test_database import disposable_database, migrate  # noqa: E402

from app.db.seeds import seed_database  # noqa: E402

KEY = f"ac:t05:budget:{FIELD_SOURCE_AUTHORIZATION}:agent"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-seconds", type=int, default=0)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    assert 0 <= args.review_seconds <= 14400
    base = Settings()
    assert not any(
        getattr(base, k)
        for k in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    assert base.deepseek_api_key.get_secret_value()
    catalog_path = PROJECT_ROOT / "docs/fixtures/T17A/cases.json"
    catalog = json.loads(catalog_path.read_bytes())
    assert args.case is None or set(args.case) <= {spec["key"] for spec in catalog["cases"]}
    selected = [spec for spec in catalog["cases"] if not args.case or spec["key"] in args.case]
    marker = uuid4().hex[:12]
    output = PROJECT_ROOT / "docs/evidence/T17A" / ("real-" + marker)
    local = PROJECT_ROOT / ".local/t17a" / marker
    output.mkdir(parents=True)
    local.mkdir(parents=True)
    settings = base.model_copy(
        update={
            "executor_allow_paid_model_calls": True,
            "executor_budget_authorization_id": FIELD_SOURCE_AUTHORIZATION,
            "executor_agent_budget_usd": 1.0,
            "executor_agent_budget_unlimited": False,
            "executor_namespace": "ac:t17a:" + marker,
        }
    )
    client = Redis.from_url(base.redis_url, socket_timeout=15)

    def ledger(key=KEY):
        return {k.decode(): int(v) for k, v in client.hgetall(key).items()}

    before = json.loads((output.parent / "preparation.json").read_bytes())
    assert {key: ledger(key) for key in before["ledgers"]} == before["ledgers"]
    assert ledger().get("limit") == 1_000_000
    report = {
        "mode": "real_DeepSeek_production_in_process_executor_SQL_Redis",
        "marker": marker,
        "authorization": FIELD_SOURCE_AUTHORIZATION,
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "ledger_before": ledger(),
        "source": catalog["source"],
        "fixture_review_boundary": "seeded reviewer role for mechanical fixtures, not human labels",
        "scenarios": [],
        "selected_cases": [spec["key"] for spec in selected],
        "price_checked_on": "2026-10-30",
        "price_source": "https://api-docs.deepseek.com/quick_start/pricing/",
    }

    def save(name, value):
        (output / name).write_text(
            json.dumps(redact(value, base), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    sessions, processes = [], []
    runtime = None
    try:
        with disposable_database("T17A") as engine:
            migrate(engine)
            seed_database(engine, base.demo_seed_password.get_secret_value())
            runtime = AgentRuntime(
                engine,
                client,
                settings,
                auto_register=True,
                prefix=settings.executor_namespace + ":session",
            )
            save("version.json", runtime.manifest)
            with zipfile.ZipFile(
                output / "source-candidate.zip", "w", zipfile.ZIP_DEFLATED
            ) as source:
                for name, expected in runtime.manifest["sources"].items():
                    data = (PROJECT_ROOT / name).read_text(encoding="utf-8").encode()
                    assert hashlib.sha256(data).hexdigest() == expected
                    source.writestr(name, data)
            save("inputs.json", catalog)
            save("session.json", {"database": engine.url.database, "marker": marker})
            service, reader = RunService(engine, settings), TraceReader(engine, settings)
            archive = zipfile.ZipFile(output / "raw-evidence.zip", "w", zipfile.ZIP_DEFLATED)
            hashes = {}

            def member(name, data):
                if not isinstance(data, bytes):
                    data = json.dumps(redact(data, base), ensure_ascii=False, default=str).encode()
                archive.writestr(name, data)
                hashes[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

            try:
                for spec in selected:
                    if ledger().get("calls_started", 0) >= 80:
                        report["stopped"] = "authorization_call_limit"
                        break
                    with Session(engine) as session, session.begin():
                        reviewer_id = session.scalar(select(User.id).where(User.role == "reviewer"))
                        case = TestCase(
                            case_key="T17A-" + spec["key"].upper() + "-" + marker.upper(),
                            revision=1,
                            family_key=catalog["family"],
                            category="robustness",
                            dataset_split="dev",
                            source=catalog["source"],
                            turns_json=[{"user": value} for value in spec["turns"]],
                            fixture_json={
                                "fixture": "booking-default-v1",
                                "reference_clock": catalog["reference_clock"],
                            },
                            expected_json={
                                "schema_version": "1.1",
                                "assertions": [
                                    {"type": "booking_count", "equals": spec["bookings"]}
                                ],
                            },
                            content_hash="0" * 64,
                            review_status="approved",
                            reviewer_id=reviewer_id,
                            reviewed_at=datetime.now(UTC),
                        )
                        # Mechanical developer fixtures; seeded role is not independent review.
                        case.content_hash = canonical_hash(case_content(case))
                        session.add(case)
                        session.flush()
                        case_id = case.id
                    run_id = service.create(
                        1,
                        CreateRun(
                            request_id=uuid4(),
                            target_version_id=runtime.version_id,
                            case_revision_ids=[case_id],
                            budgets={"agent_microusd": 100_000},
                            limits={
                                "timeout_seconds": 300,
                                "max_steps": 100,
                                "max_model_calls": 20,
                                "max_tokens": 500_000,
                            },
                        ),
                    )
                    execution = service.read(1, run_id)["executions"][0]
                    eid, sid = execution["execution_id"], execution["session_id"]
                    sessions.append(sid)
                    print(json.dumps({"starting": spec["key"], "run": run_id}), flush=True)
                    outcome = run_execution(engine, client, settings, eid)
                    snapshot = reader.snapshot(1, run_id)
                    events, cursor = [], None
                    while True:
                        page = reader.page(1, run_id, after=cursor)
                        events.extend(page["items"])
                        if not page["has_more"]:
                            break
                        cursor = page["next_cursor"]
                    _, state, _ = runtime.snapshot(1, sid)
                    with Session(engine) as session:
                        bookings = list(
                            session.scalars(
                                select(Booking).where(Booking.scope_id == execution["scope_id"])
                            )
                        )
                        drafts = list(
                            session.scalars(
                                select(BookingDraft).where(
                                    BookingDraft.scope_id == execution["scope_id"]
                                )
                            )
                        )
                        sql = [
                            {c.name: getattr(row, c.name) for c in row.__table__.columns}
                            for row in bookings
                        ]
                        draft_sql = [
                            {c.name: getattr(row, c.name) for c in row.__table__.columns}
                            for row in drafts
                        ]
                        current_run = service.owned_run(session, 1, run_id)
                        for row in session.scalars(
                            select(Artifact).where(Artifact.run_id == current_run.id)
                        ):
                            member(
                                "artifacts/" + row.relative_path,
                                (PROJECT_ROOT / row.relative_path).read_bytes(),
                            )
                    case_path = spec["key"] + "/"
                    member(case_path + "run.json", snapshot)
                    member(case_path + "events.json", events)
                    member(case_path + "state.json", state)
                    member(case_path + "sql.json", {"bookings": sql, "drafts": draft_sql})
                    calls = PROJECT_ROOT / ".local/t05/model-calls" / run_id / eid
                    if calls.exists():
                        for path in sorted(calls.glob("*.json")):
                            member(case_path + "provider/" + path.name, path.read_bytes())
                    count_ok = len(bookings) == spec["bookings"]
                    people_ok = (
                        all(row.attendees == spec["attendees"] for row in bookings)
                        if spec.get("attendees")
                        else True
                    )
                    no_pending = (
                        not any(row.status == "pending" for row in drafts)
                        if not spec["bookings"]
                        else True
                    )
                    completed = outcome.get("status") not in ("error", "cancelled", "waiting_input")
                    completed = completed and snapshot["executions"][0]["timing"].get(
                        "cursor"
                    ) == len(spec["turns"])
                    record = {
                        "case": spec["key"],
                        "run_id": run_id,
                        "execution_id": eid,
                        "outcome": outcome,
                        "bookings": len(bookings),
                        "expected_bookings": spec["bookings"],
                        "count_matches": count_ok,
                        "people_match": people_ok,
                        "invalid_has_no_pending": no_pending,
                        "fields": state.get("fields"),
                        "reply": (snapshot["executions"][0].get("output") or {}).get(
                            "assistant_text"
                        ),
                        "all_turns_observed_without_infrastructure_error": completed,
                        "matches_declared_business_checks": count_ok
                        and people_ok
                        and no_pending
                        and completed,
                    }
                    report["scenarios"].append(record)
                    save("summary.json", {**report, "ledger_after": ledger()})
                    print(
                        json.dumps(
                            {
                                "finished": spec["key"],
                                "matches": record["matches_declared_business_checks"],
                                "calls": ledger().get("calls_started"),
                            }
                        ),
                        flush=True,
                    )
            finally:
                archive.close()
                save("archive-manifest.json", hashes)
                report["ledger_after"] = ledger()
                report["old_ledgers_unchanged"] = {
                    k: ledger(k) for k in before["ledgers"]
                } == before["ledgers"]
                save("summary.json", report)
            if args.review_seconds:
                for port in (18018, 5185):
                    with socket.socket() as probe:
                        probe.bind(("127.0.0.1", port))
                env = os.environ.copy()
                for key in tuple(env):
                    if key.endswith("API_KEY"):
                        env.pop(key)
                env.update(
                    {
                        "MYSQL_DATABASE": engine.url.database,
                        "DEEPSEEK_API_KEY": "",
                        "ALLOW_PAID_MODEL_CALLS": "false",
                        "EXECUTOR_ALLOW_PAID_MODEL_CALLS": "false",
                        "CASEGEN_ALLOW_PAID_MODEL_CALLS": "false",
                        "JUDGE_ALLOW_PAID_MODEL_CALLS": "false",
                        "AC_API_PROXY_TARGET": "http://127.0.0.1:18018",
                        "PYTHONPATH": str(PROJECT_ROOT / "backend"),
                    }
                )
                commands = [
                    (
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "source_checks.review_api:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "18018",
                        ],
                        PROJECT_ROOT / "backend",
                    ),
                    (
                        [
                            "D:/node.js/node.exe",
                            str(PROJECT_ROOT / "frontend/node_modules/vite/bin/vite.js"),
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "5185",
                            "--strictPort",
                        ],
                        PROJECT_ROOT / "frontend",
                    ),
                ]
                for index, (command, cwd) in enumerate(commands):
                    log = open(local / f"review-{index}.txt", "w", encoding="utf-8")
                    processes.append(
                        subprocess.Popen(
                            command,
                            cwd=cwd,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                    )
                    log.close()
                for _ in range(80):
                    try:
                        if (
                            httpx.get(
                                "http://127.0.0.1:18018/api/v1/health/live", timeout=2
                            ).status_code
                            == 200
                        ):
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError("Owned review API startup failed")
                now = datetime.now(UTC)
                review = {
                    "marker": marker,
                    "ui": "http://127.0.0.1:5185",
                    "api": "http://127.0.0.1:18018",
                    "started_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=args.review_seconds)).isoformat(),
                    "stop_file": str(local / "stop-review"),
                    "evidence": str(output),
                    "read_only": True,
                    "scenarios": report["scenarios"],
                }
                save("review-session.json", review)
                (PROJECT_ROOT / ".local/t17a/latest-review.json").write_text(
                    json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(json.dumps({"review": review}, ensure_ascii=False), flush=True)
                deadline = time.monotonic() + args.review_seconds
                while time.monotonic() < deadline and not (local / "stop-review").exists():
                    time.sleep(1)
    finally:
        for process in processes:
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                )
                process.wait(timeout=15)
        if runtime:
            for sid in sessions:
                if client.exists(runtime._key(sid)):
                    runtime.delete_test_session(1, sid)
        client.close()


if __name__ == "__main__":
    main()
