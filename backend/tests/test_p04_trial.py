"""Mechanical controls for the new pilot; no real participant or provider calls."""

from http.server import HTTPServer
from threading import Thread
from uuid import uuid4

import httpx
import pytest

from app.agents.versions import canonical_hash
from benchmarks.p04_trial import collector as c


class Double:
    def __init__(self):
        self.sent, self.queried, self.saved = [], [], {}
        self.fail = self.drift = False

    def check(self):
        if self.drift:
            raise ValueError("Explicit synthetic source drift")
        task = {
            "id": "U01",
            "case_id": 700001,
            "case_key": "MECH-CASE-01",
            "title": "机械控制题",
            "instruction": "这是自动化控制，不是真人试用。",
            "snapshot": {
                "content_hash": "2" * 64,
                "guidance": {
                    "case_key": "MECH-CASE-01",
                    "revision": 1,
                    "prerequisites": {
                        "reference_clock": "2030-01-01T09:00:00+08:00",
                        "fixture": "explicit-mechanical-only",
                        "knowledge_profile": "mechanical",
                        "initial_state": {
                            "active_bookings": [],
                            "rooms": [
                                {"code": "X01", "capacity": 6, "opens": "09:00", "closes": "18:00"}
                            ],
                        },
                    },
                    "turns": [{"user": "机械输入", "number": 1}],
                    "expected": {
                        "active_booking_counts": [0],
                        "boundary": "Mechanical expectation only",
                    },
                    "reviewed_reason": "Explicit synthetic fixture",
                    "assertions": [
                        {
                            "type": "booking_count",
                            "title": "机械数量检查",
                            "meaning": "只用于布局，不是执行结果。",
                            "evidence": "机械快照",
                            "parameters": {"type": "booking_count", "equals": 0},
                        }
                    ],
                },
            },
        }
        return {"package_hash": "0" * 64, "source_hash": "1" * 64, "cases": [task] * 3}

    def assistant(self, participant):
        return self

    def question(self, text):
        return text.strip()

    def ask(self, question, rid):
        self.sent.append(rid)
        self.saved[rid] = {
            "request_id": rid,
            "question_hash": canonical_hash(question),
            "mode": "explicit_mechanical_double",
            "status": "completed",
            "answer": {
                "facts": [{"text": "机械回复，不是真人证据", "citations": ["mechanical-source"]}],
                "action_ids": ["case.read", "javascript:invalid"],
            },
            "sources": [
                {
                    "tool": "read_selection",
                    "source_id": "mechanical-source",
                    "content": {
                        "kind": "case",
                        "id": "700001",
                        "content_hash": "2" * 64,
                        "locations": {
                            "prerequisites": "01",
                            "turns": "02",
                            "expected": "03",
                            "assertions": "04",
                        },
                    },
                }
            ],
        }
        if self.fail:
            raise ValueError("Explicit unknown after synthetic admission")
        return self.saved[rid]

    def receipt(self, rid):
        self.queried.append(rid)
        return self.saved[rid]


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ROOT", tmp_path / "mechanical-only")
    runtime = Double()
    folder, config = c.prepare(runtime, "P01", 18054)
    return folder, {**config, "folder": str(folder)}, runtime


def form(config, action, **values):
    return {
        key: [str(value)]
        for key, value in {"csrf": config["csrf"], "action": action, **values}.items()
    }


def start(context):
    folder, config, runtime = context
    c.mutate(
        folder,
        config,
        form(config, "start", familiarity="seen", involvement="other", consent="yes"),
        runtime,
    )


def test_http_get_does_not_start_and_origin_host_csrf_are_enforced(context):
    folder, config, runtime = context
    server = HTTPServer(("127.0.0.1", 0), c.handler(folder, config, runtime))
    config["port"] = server.server_port
    server.RequestHandlerClass = c.handler(folder, config, runtime)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/trial/{config['token']}"
    try:
        assert httpx.get(endpoint, trust_env=False).status_code == 200
        assert c.records(folder, config) == [] and runtime.sent == []
        assert (
            httpx.get(endpoint, headers={"Host": "foreign.invalid"}, trust_env=False).status_code
            == 404
        )
        # Send headers only: the missing-Origin guard must reject before reading a form.
        # Closing an unread request body can reset Windows TCP before httpx sees the 403.
        from http.client import HTTPConnection

        rejected = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            rejected.putrequest("POST", f"/trial/{config['token']}")
            rejected.putheader("Content-Length", "100")
            rejected.putheader("Content-Type", "application/x-www-form-urlencoded")
            rejected.endheaders()
            assert rejected.getresponse().status == 403
        finally:
            rejected.close()
        assert (
            httpx.post(
                endpoint,
                headers={"Origin": f"http://127.0.0.1:{server.server_port}"},
                data={"action": "start", "csrf": "wrong"},
                trust_env=False,
            ).status_code
            == 409
        )
        assert c.records(folder, config) == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_explicit_send_is_once_only_and_human_answer_is_separate_then_sealed(context):
    folder, config, runtime = context
    start(context)
    rid = str(uuid4())
    data = form(config, "ask", request_id=rid, question="机械问题")
    c.mutate(folder, config, data, runtime)
    c.mutate(folder, config, data, runtime)
    assert runtime.sent == [rid]
    rows = c.records(folder, config)
    body = c.render(config, rows, "/trial/control").decode()
    assert 'href="javascript:' not in body
    assert (
        '<textarea id="judgment"' in body
        and 'name="judgment" rows="3" maxlength="4000"></textarea>' in body
    )
    c.mutate(
        folder,
        config,
        form(
            config,
            "finish",
            judgment="机械答案",
            evidence="机械证据",
            next_action="只读",
            status="uncertain",
            seal="yes",
            reason="",
        ),
        runtime,
    )
    sealed = c.records(folder, config)[-1]
    assert sealed["kind"] == "answer_seal"
    assert sealed["data"]["scoring"] == "not_performed_reference_pending"
    with pytest.raises(ValueError, match="sealed"):
        c.mutate(
            folder, config, form(config, "ask", request_id=uuid4(), question="不得调用"), runtime
        )
    assert runtime.sent == [rid]


def test_unknown_repost_queries_original_receipt_and_never_resends(context):
    folder, config, runtime = context
    start(context)
    runtime.fail = True
    rid = str(uuid4())
    data = form(config, "ask", request_id=rid, question="机械未知问题")
    with pytest.raises(ValueError, match="unknown"):
        c.mutate(folder, config, data, runtime)
    with pytest.raises(ValueError, match="original receipt"):
        c.mutate(
            folder, config, form(config, "ask", request_id=uuid4(), question="不得换键"), runtime
        )
    c.mutate(folder, config, data, runtime)
    assert runtime.sent == [rid] and runtime.queried == [rid]
    assert c.records(folder, config)[-1]["kind"] == "receipt"


def test_receipt_tampering_is_not_displayed_or_adopted(context):
    folder, config, runtime = context
    start(context)
    rid = str(uuid4())
    c.mutate(folder, config, form(config, "ask", request_id=rid, question="机械问题"), runtime)
    c.receipt_path(folder, rid).write_text('{"request_id":"tampered"}', encoding="utf-8")
    with pytest.raises(ValueError, match="receipt changed"):
        c.records(folder, config)


def test_orphan_receipt_requires_original_readback_before_chain_binding(context, monkeypatch):
    folder, config, runtime = context
    start(context)
    real_emit = c.emit

    def crash(*args):
        if args[3] == "receipt":
            raise ValueError("Explicit crash between saved receipt and event")
        return real_emit(*args)

    monkeypatch.setattr(c, "emit", crash)
    rid = str(uuid4())
    data = form(config, "ask", request_id=rid, question="机械问题")
    with pytest.raises(ValueError, match="crash"):
        c.mutate(folder, config, data, runtime)
    assert c.read_receipt(folder, c.records(folder, config), rid) is None
    monkeypatch.setattr(c, "emit", real_emit)
    c.mutate(folder, config, data, runtime)
    assert runtime.sent == [rid] and runtime.queried == [rid]


def test_field_errors_escape_and_retain_draft_withdraw_preserves_without_faking_seal(context):
    folder, config, runtime = context
    start(context)
    data = form(
        config,
        "finish",
        judgment="<script>control</script>",
        evidence="",
        next_action="只读",
        status="blocked",
        reason="",
    )
    with pytest.raises(c.FormError) as error:
        c.mutate(folder, config, data, runtime)
    body = c.render(
        config, c.records(folder, config), "/trial/control", draft=data, errors=error.value.fields
    ).decode()
    assert "&lt;script&gt;control&lt;/script&gt;" in body and 'aria-invalid="true"' in body
    data["withdraw"] = ["yes"]
    c.mutate(folder, config, data, runtime)
    last = c.records(folder, config)[-1]
    assert last["kind"] == "withdraw" and last["data"]["human_seal"] is False
    assert last["data"]["draft"]["judgment"] == "<script>control</script>"
    assert runtime.sent == []


def test_event_hash_and_entry_identity_cannot_be_replaced(context):
    folder, config, runtime = context
    start(context)
    with pytest.raises(ValueError, match="entry differs"):
        c.prepare(runtime, "P01", 18055)
    text = (folder / "events.jsonl").read_text(encoding="utf-8")
    (folder / "events.jsonl").write_text(text.replace('"seen"', '"new"'), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        c.records(folder, config)


def test_question_and_receipt_preserve_draft_without_treating_it_as_final_answer(context):
    folder, config, runtime = context
    start(context)
    rid = str(uuid4())
    c.mutate(
        folder,
        config,
        form(
            config,
            "ask",
            request_id=rid,
            question="机械问题",
            judgment="先写的草稿",
            evidence="尚未找全",
            next_action="继续阅读",
            status="uncertain",
        ),
        runtime,
    )
    rows = c.records(folder, config)
    assert any(row["kind"] == "draft" for row in rows)
    assert not any(row["kind"] == "answer_seal" for row in rows)
    assert "先写的草稿</textarea>" in c.render(config, rows, "/trial/control").decode()
    c.mutate(
        folder,
        config,
        form(
            config,
            "receipt:" + rid,
            request_id=uuid4(),
            judgment="后来补充",
            evidence="机械证据",
            next_action="只读",
            status="blocked",
        ),
        runtime,
    )
    assert (
        "后来补充</textarea>"
        in c.render(config, c.records(folder, config), "/trial/control").decode()
    )
    assert runtime.sent == [rid]
