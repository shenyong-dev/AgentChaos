"""Bounded approved T17D HTTP checks; save every input and failure, never reset usage."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, Settings
from app.core.passwords import hash_password
from app.db.engine import create_database_engine
from app.interactive.budget import AUTHORIZATION
from app.models import User


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    root, settings = PROJECT_ROOT, Settings()
    review = json.loads((root / ".local/t17d/latest-review.json").read_bytes())
    assert review["mode"] == "real_provider"
    folder = root / review["evidence"]
    results_file = folder / "real-results.json"
    assert not results_file.exists(), "Do not rerun or overwrite prior real results"
    cases_path = root / "docs/fixtures/T17D/development-scenarios.json"
    cases = json.loads(cases_path.read_bytes())["cases"]
    client = Redis.from_url(settings.redis_url)
    baseline = json.loads((root / "docs/evidence/T17D/preparation.json").read_bytes())
    key = f"ac:t17d:budget:{AUTHORIZATION}:agent"

    def ledger():
        return {k.decode(): int(v) for k, v in client.hgetall(key).items()}

    def old_ledgers():
        return {
            k: {a.decode(): int(b) for a, b in client.hgetall(k).items()}
            for k in baseline["ledgers"]
        }

    assert old_ledgers() == baseline["ledgers"]
    results = {
        "started_at": datetime.now(UTC).isoformat(),
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "api_version": review["version_id"],
        "config_hash": review["config_hash"],
        "helper_source_sha256": hashlib.sha256(
            Path(__file__).with_name("server.py").read_bytes()
        ).hexdigest(),
        "ledger_before": ledger(),
        "cases": [],
    }
    write(results_file, results)
    with httpx.Client(
        base_url=review["api"],
        auth=("demo_operator", settings.demo_seed_password.get_secret_value()),
        timeout=160,
    ) as api:
        capabilities = api.get("/api/v1/interactive/access").json()["data"]
        assert capabilities["model_actions_available"] and capabilities["mode"] == "real_provider"
        for case in cases[:6]:
            record = {"id": case["id"], "name": case["name"], "steps": [], "passed": False}
            results["cases"].append(record)
            write(results_file, results)

            def request(path, body=None, *, record=record):
                step = {"path": path, "body": body, "started_at": datetime.now(UTC).isoformat()}
                record["steps"].append(step)
                write(results_file, results)
                response = api.get(path) if body is None else api.post(path, json=body)
                step.update(
                    http_status=response.status_code,
                    response=response.json(),
                    finished_at=datetime.now(UTC).isoformat(),
                )
                write(results_file, results)
                response.raise_for_status()
                return response.json()["data"]

            try:
                view = request(
                    "/api/v1/interactive/sessions",
                    {"request_id": str(uuid4()), "version_id": capabilities["version_id"]},
                )
                session_id = view["session_id"]
                record["session_id"] = session_id
                old_draft = None
                for index, text in enumerate(case["inputs"]):
                    view = request(
                        f"/api/v1/interactive/sessions/{session_id}/messages",
                        {
                            "request_id": str(uuid4()),
                            "expected_revision": view["revision"],
                            "text": text,
                        },
                    )
                    if index == 0:
                        old_draft = view.get("pending_draft")
                        if case.get("first_has_no_draft"):
                            assert (
                                old_draft is None and view["sql_observation"]["booking_count"] == 0
                            )
                assert view["status"] == "waiting_confirmation" and view.get("pending_draft"), (
                    "No complete confirmation draft"
                )
                draft = view["pending_draft"]
                if case.get("supersedes_old_draft"):
                    assert draft["draft_id"] != old_draft["draft_id"]
                    assert (
                        view["fields"]["attendees"]["value"] == 12
                        and view["fields"]["attendees"]["source_turn"] == 2
                    )
                    assert any(
                        d["draft_id"] == old_draft["draft_id"] and d["status"] == "superseded"
                        for d in view["sql_observation"]["drafts"]
                    )
                if case.get("controlled_tool_fault"):
                    write(Path(review["control_file"]), {"sessions": [session_id]})
                    record["fault_boundary"] = case["boundary"]
                body = {
                    "request_id": str(uuid4()),
                    "expected_revision": view["revision"],
                    "decision": case["decision"],
                    "draft_id": draft["draft_id"],
                    "draft_hash": draft["draft_hash"],
                    "confirmation_revision": view["confirmation_revision"],
                }
                view = request(f"/api/v1/interactive/sessions/{session_id}/resume", body)
                if case.get("controlled_tool_fault") and view.get("unknown_submission"):
                    view = request(
                        f"/api/v1/interactive/sessions/{session_id}/messages",
                        {
                            "request_id": str(uuid4()),
                            "expected_revision": view["revision"],
                            "text": "请只按原请求标识查证提交结果，不要重新提交或创建新预约。",
                        },
                    )
                assert view["sql_observation"]["available"]
                assert view["sql_observation"]["booking_count"] == case["expected_bookings"]
                if case["expected_bookings"]:
                    assert (
                        view["sql_observation"]["bookings"][0]["attendees"]
                        == case["expected_attendees"]
                    )
                if case["decision"] == "reject":
                    assert view["confirmation_state"] == "rejected"
                if case.get("replay_same_request"):
                    before = ledger()
                    receipt = request(
                        f"/api/v1/interactive/sessions/{session_id}/receipts/{body['request_id']}"
                    )
                    assert receipt["status"] == "completed"
                    view = request(f"/api/v1/interactive/sessions/{session_id}/resume", body)
                    assert ledger() == before and view["sql_observation"]["booking_count"] == 1
                    record["http_loss_boundary"] = (
                        "Response ignored after completion; original receipt and exact replay; "
                        "actual browser network abort separately verified with offline gateway."
                    )
                if case.get("controlled_tool_fault"):
                    trace = request(f"/api/v1/interactive/sessions/{session_id}/trace?limit=100")
                    assert any(
                        x.get("type") == "tool"
                        and x.get("result", {}).get("code")
                        == "controlled_after_commit_receipt_loss"
                        for x in trace["items"]
                    )
                    assert any(x.get("name") == "lookup_booking_by_request" for x in trace["items"])
                    assert not view.get("unknown_submission")
                    write(Path(review["control_file"]), {"sessions": []})
                record.update(passed=True, final_view=view)
            except Exception as exc:
                record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            finally:
                record["ledger_after"] = ledger()
                assert old_ledgers() == baseline["ledgers"]
                write(results_file, results)
                print(
                    json.dumps(
                        {
                            "case": case["id"],
                            "passed": record["passed"],
                            "calls": ledger().get("calls_started"),
                        }
                    ),
                    flush=True,
                )
            if ledger().get("calls_started", 0) >= 120:
                break
        # Free access/role/version controls on this real API, without model calls.
        control_before = ledger()
        first = results["cases"][0]["session_id"]
        invalid = api.post(
            "/api/v1/interactive/sessions", json={"request_id": str(uuid4()), "version_id": 999999}
        )
        trusted = api.post(
            "/api/v1/interactive/sessions",
            json={
                "request_id": str(uuid4()),
                "version_id": capabilities["version_id"],
                "scope_id": "demo",
            },
        )
        reviewer = api.get(
            "/api/v1/interactive/access",
            auth=("demo_reviewer", settings.demo_seed_password.get_secret_value()),
        )
        legacy = api.get(f"/api/v1/demo/sessions/{first}")
        controls = {
            "wrong_version_status": invalid.status_code,
            "trusted_context_status": trusted.status_code,
            "reviewer_status": reviewer.status_code,
            "legacy_protocol_status": legacy.status_code,
        }
        assert controls == {
            "wrong_version_status": 409,
            "trusted_context_status": 400,
            "reviewer_status": 403,
            "legacy_protocol_status": 404,
        }
        assert ledger() == control_before
        results["free_api_controls"] = controls
        assert review["database"].startswith("agentchaos_t17d_verify_")
        engine = create_database_engine(settings, database=review["database"])
        try:
            with Session(engine) as session, session.begin():
                assert (
                    session.scalar(select(User.id).where(User.username == "t17d_other_operator"))
                    is None
                )
                session.add(
                    User(
                        username="t17d_other_operator",
                        role="operator",
                        enabled=True,
                        password_hash=hash_password(settings.demo_seed_password.get_secret_value()),
                    )
                )
        finally:
            engine.dispose()
        other_auth = ("t17d_other_operator", settings.demo_seed_password.get_secret_value())
        owner_checks = {}
        for suffix in ["", "/history", "/trace", "/receipts/" + str(uuid4())]:
            response = api.get(f"/api/v1/interactive/sessions/{first}" + suffix, auth=other_auth)
            owner_checks[suffix or "/view"] = response.status_code
        response = api.post(
            f"/api/v1/interactive/sessions/{first}/messages",
            auth=other_auth,
            json={"request_id": str(uuid4()), "expected_revision": 0, "text": "预约"},
        )
        owner_checks["/messages"] = response.status_code
        assert all(value == 404 for value in owner_checks.values())
        assert ledger() == control_before
        results["cases"].extend(
            [
                {
                    "id": "D07",
                    "passed": True,
                    "model_requests": 0,
                    "owner_checks": owner_checks,
                    "reviewer_status": controls["reviewer_status"],
                    "legacy_protocol_status": controls["legacy_protocol_status"],
                },
                {
                    "id": "D08",
                    "passed": True,
                    "model_requests": 0,
                    "wrong_version_status": controls["wrong_version_status"],
                    "trusted_context_status": controls["trusted_context_status"],
                    "old_session_metadata_and_actual_database_isolation": (
                        "separately verified by free integration tests"
                    ),
                },
            ]
        )
    results.update(
        finished_at=datetime.now(UTC).isoformat(),
        ledger_after=ledger(),
        old_thirteen_ledgers_unchanged=old_ledgers() == baseline["ledgers"],
    )
    write(results_file, results)
    client.close()


if __name__ == "__main__":
    main()
