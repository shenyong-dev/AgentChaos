"""Explicitly paid T04 development dialogues; isolated DB, real global budget ledger.

Run from backend: python -m scripts.verify_agent_dialogues [--case D01 ...].
No provider retries, synthetic model responses, production bookings or ledger reset.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from langchain_core.messages import messages_to_dict
from redis import Redis
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.agents.errors import AgentError
from app.agents.runtime import AgentRuntime
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.db.fixtures import initialize_fixture
from app.db.seeds import seed_database
from app.models import Booking, BookingDraft
from app.schemas.booking import ConfirmBooking, PreviewBooking
from app.services.booking import BookingContext

REFERENCE = datetime(2026, 10, 25, 1, tzinfo=UTC)


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def action(view, text):
    return {"request_id": str(uuid4()), "expected_revision": view["revision"], "text": text}


def decision(view, value="confirm"):
    draft = view["pending_draft"]
    assert draft, "Expected a genuine preview_booking draft"
    return {
        "request_id": str(uuid4()),
        "expected_revision": view["revision"],
        "decision": value,
        "draft_id": draft["draft_id"],
        "draft_hash": draft["draft_hash"],
        "confirmation_revision": view["confirmation_revision"],
    }


@contextmanager
def verification_database(settings, evidence):
    name = "agentchaos_t04_verify_" + uuid4().hex[:12]
    assert name != settings.mysql_database and name.startswith("agentchaos_t04_verify_")
    admin = create_database_engine(settings, database="mysql")
    engine = None
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        engine = create_database_engine(settings, database=name)
        with engine.connect() as connection:
            config = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        seed_database(engine, "synthetic-T04-test-password")
        yield engine, name
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS `{name}`")
            removed = (
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:name"
                    ),
                    {"name": name},
                )
                == 0
            )
        dump(evidence / "cleanup.json", {"temporary_database": name, "removed": removed})
        admin.dispose()


class DialogueSuite:
    def __init__(self, engine, redis, settings, root, database):
        self.engine, self.redis, self.settings, self.root, self.database = (
            engine,
            redis,
            settings,
            root,
            database,
        )
        self.runtime = AgentRuntime(
            engine,
            redis,
            settings,
            auto_register=True,
            clock=lambda: REFERENCE,
            prefix="ac:t04:live:" + root.name,
        )
        self.runtime.gateway.audit_directory = root / "model-calls"
        self.sessions = []
        self.count = 0
        self.case = ""
        dump(root / "v0-manifest.json", self.runtime.manifest)
        (root / "graph.mmd").write_text(
            self.runtime.graph.get_graph().draw_mermaid(), encoding="utf-8"
        )

    def create(self, scope=None):
        scope = scope or "test:t04:live:" + uuid4().hex
        with Session(self.engine) as session, session.begin():
            initialize_fixture(session, scope)
        view = self.runtime.create(1, scope_id=scope, reference_clock=REFERENCE)
        self.sessions.append(view["session_id"])
        return view

    def save(self, view):
        self.count += 1
        _, state, snapshot = self.runtime.snapshot(1, view["session_id"])
        dump(
            self.root / self.case / f"turn-{self.count:03d}.json",
            {
                "view": view,
                "state": {**state, "messages": messages_to_dict(state["messages"])},
                "next": snapshot.next,
            },
        )
        for item in state["audit"]:
            if item["type"] == "tool":
                source = PROJECT_ROOT / ".local/t04/tool-evidence" / (item["evidence_id"] + ".json")
                if source.exists():
                    dump(
                        self.root / "tool-evidence" / source.name,
                        json.loads(source.read_text(encoding="utf-8")),
                    )
        return view

    def say(self, view, text):
        return self.save(self.runtime.act(1, view["session_id"], action(view, text)))

    def decide(self, view, value="confirm"):
        return self.save(
            self.runtime.act(1, view["session_id"], decision(view, value), kind="resume")
        )

    def booked(self, view, count=1):
        assert view["business_result"]["commit_state"] == "committed", view["business_result"]
        assert view["business_result"]["data"]["booking_id"]
        assert self.bookings(view) == count
        assert not re.search(
            r"(没有|未|不曾)\s*(?:再)?调用.{0,40}confirm_booking", view["assistant_text"], re.S
        ), "Answer contradicted an actual confirm_booking execution"

    def bookings(self, view):
        scope = self.runtime._metadata(1, view["session_id"])["scope_id"]
        with Session(self.engine) as session:
            return session.scalar(
                select(func.count()).select_from(Booking).where(Booking.scope_id == scope)
            )

    def pending(self, view):
        assert view["status"] == "waiting_confirmation", view["assistant_text"]
        assert view["pending_draft"] and self.bookings(view) == 0
        return view

    def D01(self):
        view = self.pending(self.say(self.create(), "请预约明天A101，14:00到15:00，10人。"))
        self.booked(self.decide(view))

    def D02(self):
        view = self.say(self.create(), "我想预约明天下午，10人。")
        assert "start_time" not in view["fields"] and view["pending_draft"] is None
        view = self.pending(self.say(view, "下午14:00到15:00，选A101。"))
        assert view["fields"]["attendees"]["source_turn"] == 1
        view = self.pending(self.say(view, "时长改成2小时，其他保持。"))
        assert view["fields"]["duration_minutes"]["value"] == 120
        assert "end_time" not in view["fields"]
        assert datetime.fromisoformat(view["pending_draft"]["end_at"]).hour == 16
        self.booked(self.decide(view))

    def D03(self):
        view = self.pending(self.say(self.create(), "请预约明天A101，14:00到15:00，10人。"))
        old = decision(view)
        view = self.pending(self.say(view, "人数改为30人，场地改B201，其他保持。"))
        assert view["fields"]["attendees"]["value"] == 30
        assert view["fields"]["attendees"]["source_turn"] == 2
        assert view["fields"]["date"]["source_turn"] == 1
        try:
            self.runtime.act(
                1, view["session_id"], {**old, "expected_revision": view["revision"]}, kind="resume"
            )
            raise AssertionError("Old draft unexpectedly authorized")
        except AgentError as exc:
            assert exc.code == "confirmation_stale"
            dump(
                self.root / self.case / "stale-confirmation.json",
                {"rejected": True, "code": exc.code},
            )
        self.booked(self.decide(view))

    def D04(self):
        view = self.pending(self.say(self.create(), "请预约明天A101，14:00到15:00，10人。"))
        view = self.pending(self.say(view, "改成后天15:00到16:00，其他保持。"))
        assert view["fields"]["date"]["value"] == "2026-10-27"
        assert view["fields"]["start_time"]["value"] == "15:00"
        assert view["fields"]["attendees"]["source_turn"] == 1
        self.booked(self.decide(view))

    def D05(self):
        view = self.pending(self.say(self.create(), "请预约明天A101，14:00到15:00，10人。"))
        view = self.decide(view, "reject")
        assert view["confirmation_state"] == "rejected" and view["denials"]
        assert view["pending_draft"] is None and self.bookings(view) == 0
        view = self.say(view, "请回顾一下我刚才的决定。")
        assert view["confirmation_state"] == "rejected" and self.bookings(view) == 0

    def D06(self):
        view = self.say(
            self.create(), "请查实验规则，A101的押金和收费金额是多少？只回答有证据的信息。"
        )
        assert view["last_retrieval"] and view["last_retrieval"]["status"] in (
            "no_lexical_match",
            "low_relevance",
        )
        assert view["pending_draft"] is None and self.bookings(view) == 0

    def D07(self):
        view = self.pending(self.say(self.create(), "请预约明天A101，14:00到15:00，10人。"))
        metadata = self.runtime._metadata(1, view["session_id"])
        context = BookingContext(
            metadata["scope_id"], "test:competing-user", REFERENCE, str(uuid4())
        )
        request = PreviewBooking(
            start_at=REFERENCE + timedelta(days=1, hours=5),
            end_at=REFERENCE + timedelta(days=1, hours=6),
            attendees=10,
            room_code="A101",
        )
        competing = self.runtime.booking.preview_booking(context, request).data
        self.runtime.booking.confirm_booking(
            context,
            ConfirmBooking(
                draft_id=competing.draft_id,
                draft_hash=competing.draft_hash,
                idempotency_key=competing.suggested_idempotency_key,
                confirmed=True,
            ),
        )
        view = self.decide(view)
        _, state, _ = self.runtime.snapshot(1, view["session_id"])
        errors = [
            item
            for item in state["audit"]
            if item["type"] == "tool" and item["result"].get("code") == "slot_conflict"
        ]
        assert errors, "No actual deterministic slot_conflict was observed"
        assert self.bookings(view) == 1 and view["business_result"] is None

    def D08(self):
        view = self.pending(self.say(self.create(), "请预约明天A101，14:00到15:00，10人。"))
        env = {**os.environ, "MYSQL_DATABASE": self.database, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.verify_agent_dialogues",
                "--resume-session",
                view["session_id"],
                "--prefix",
                self.runtime.prefix,
                "--evidence",
                str(self.root),
            ],
            cwd=PROJECT_ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=150,
        )
        dump(
            self.root / self.case / "process-restart.json",
            {
                "parent_pid": os.getpid(),
                "child_exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        assert result.returncode == 0, result.stderr
        self.booked(self.save(self.runtime.view(1, view["session_id"])))

    def D09(self):
        first = self.create()
        scope = self.runtime._metadata(1, first["session_id"])["scope_id"]
        # The existing fixture is shared; creating a second session must not reset it.
        second = self.runtime.create(1, scope_id=scope, reference_clock=REFERENCE)
        self.sessions.append(second["session_id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.runtime.act, 1, view["session_id"], action(view, text))
                for view, text in [
                    (first, "请预约明天A101，14:00到15:00，10人。"),
                    (second, "请预约明天B201，15:00到16:00，30人。"),
                ]
            ]
            first, second = [self.save(future.result()) for future in futures]
        self.pending(first)
        self.pending(second)
        assert (
            first["fields"]["attendees"]["value"] == 10
            and second["fields"]["attendees"]["value"] == 30
        )
        with Session(self.engine) as session:
            drafts = list(
                session.scalars(select(BookingDraft).where(BookingDraft.scope_id == scope))
            )
            assert len(drafts) == 2 and all(draft.status == "pending" for draft in drafts)
        forged = {**decision(first), "expected_revision": second["revision"]}
        try:
            self.runtime.act(1, second["session_id"], forged, kind="resume")
            raise AssertionError("Cross-session confirmation accepted")
        except AgentError as exc:
            assert exc.code == "confirmation_stale"
        self.booked(self.decide(first))
        self.booked(self.decide(second), count=2)

    def D10(self):
        view = self.pending(self.say(self.create(), "请预约明天A101，14:00到15:00，10人。"))
        view = self.pending(self.say(view, "人数改成30人，场地改B201，其他保持。"))
        view = self.decide(view, "reject")
        for index in range(5):
            noise = (
                f"以下是第{index + 1}段背景记录，只作闲聊，不改变预约字段。"
                + "我们讨论团队沟通、会议材料整理、设备摆放和交流流程。这些文字不包含新的预约要求。"
                * 45
                + "请只简短回复收到，保留我之前的修改和拒绝，不要提交。"
            )
            view = self.say(view, noise)
        view = self.say(view, "现在请回顾最后确定的人数、场地以及我是否同意提交。不要提交。")
        assert view["compactions"] > 0 and view["history_messages"] > 20
        assert (
            view["fields"]["attendees"]["value"] == 30
            and view["fields"]["attendees"]["source_turn"] == 2
        )
        assert view["fields"]["room_code"]["value"] == "B201"
        assert view["confirmation_state"] == "rejected" and view["denials"][0]["source_turn"] == 3
        assert self.bookings(view) == 0

    def run(self, cases):
        results = []
        before = self.runtime.budget.snapshot()
        try:
            for case in cases:
                self.case = case
                started = datetime.now(UTC).isoformat()
                print(json.dumps({"case": case, "status": "running"}), flush=True)
                try:
                    getattr(self, case)()
                    result = {"case": case, "status": "passed", "started_at": started}
                except Exception as exc:
                    result = {
                        "case": case,
                        "status": "failed",
                        "started_at": started,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                    for session_id in self.sessions:
                        try:
                            self.save(self.runtime.view(1, session_id))
                        except Exception:
                            pass
                results.append(result)
                dump(
                    self.root / "summary.json",
                    {
                        "scope": "T04 real paid development dialogues; not T13 formal dataset",
                        "results": results,
                        "budget_before": before,
                        "budget_after": self.runtime.budget.snapshot(),
                    },
                )
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if result["status"] != "passed":
                    break
        finally:
            for session_id in self.sessions:
                self.runtime.delete_test_session(1, session_id)
            dump(
                self.root / "session-cleanup.json",
                {"session_ids": self.sessions, "removed": True, "budget_ledger_preserved": True},
            )
        return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", nargs="+", choices=[f"D{i:02d}" for i in range(1, 11)])
    parser.add_argument("--resume-session")
    parser.add_argument("--prefix")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    settings = Settings()
    redis = Redis.from_url(settings.redis_url, socket_timeout=15, socket_connect_timeout=5)
    if args.resume_session:
        engine = create_database_engine(settings)
        runtime = AgentRuntime(engine, redis, settings, prefix=args.prefix, clock=lambda: REFERENCE)
        runtime.gateway.audit_directory = args.evidence / "model-calls"
        view = runtime.view(1, args.resume_session)
        assert view["status"] == "waiting_confirmation"
        result = runtime.act(1, args.resume_session, decision(view), kind="resume")
        dump(
            args.evidence / "D08/child-result.json",
            {"pid": os.getpid(), "restored": view, "resumed": result},
        )
        print(
            json.dumps(
                {
                    "child_pid": os.getpid(),
                    "status": result["status"],
                    "commit_state": result["business_result"]["commit_state"],
                }
            )
        )
        engine.dispose()
    else:
        root = (
            PROJECT_ROOT
            / "docs/evidence/T04"
            / ("live-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6])
        )
        root.mkdir(parents=True)
        with verification_database(settings, root) as (engine, name):
            suite = DialogueSuite(engine, redis, settings, root, name)
            results = suite.run(args.case or [f"D{i:02d}" for i in range(1, 11)])
        print(
            json.dumps(
                {
                    "evidence": str(root),
                    "passed": sum(item["status"] == "passed" for item in results),
                    "executed": len(results),
                },
                ensure_ascii=False,
            )
        )
        if any(item["status"] != "passed" for item in results):
            raise SystemExit(1)
    redis.close()


if __name__ == "__main__":
    main()
