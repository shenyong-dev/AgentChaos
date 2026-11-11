"""Explicit recorder controls. No real participant or supplier data is created."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import HTTPServer
from threading import Thread
from uuid import uuid4

import httpx
import pytest

from app.agents.errors import AgentError
from benchmarks.t19b.preparation import protocol
from benchmarks.t19b_ui import collector as c


class ExplicitRuntimeDouble:
    def __init__(self):
        self.sent, self.queried = [], []
        self.fail = False
        self.drift = False

    def check(self):
        if self.drift:
            raise ValueError("explicit synthetic source drift")
        return {
            "protocol": protocol(),
            "protocol_hash": "1" * 64,
            "source_hash": "2" * 64,
            "package_hash": "3" * 64,
        }

    def question(self, value):
        return value.strip()

    def port(self, config, task):
        return self

    def ask(self, question, rid):
        self.sent.append((question, rid))
        if self.fail:
            raise AgentError("synthetic_unknown", "Explicit synthetic control failure", 503)
        return {
            "status": "completed",
            "mode": "offline_control",
            "answer": {
                "facts": [
                    {"text": "Explicit mechanical reply, not human evidence", "citations": []}
                ],
                "action_ids": ["case.read", "javascript:invalid-control"],
            },
            "sources": [],
            "calls": [],
            "request_id": rid,
        }

    def receipt(self, rid):
        self.queried.append(rid)
        if self.fail:
            raise AgentError("synthetic_unknown", "Explicit synthetic control failure", 503)
        return {
            "status": "failed",
            "mode": "offline_control",
            "error": "Explicit synthetic failed receipt",
            "sources": [],
            "calls": [],
            "request_id": rid,
        }


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ROOT", tmp_path / "explicit-recorder-controls")
    runtime = ExplicitRuntimeDouble()
    folder, config = c.prepare(runtime, "P01", 18037)
    return folder, config, runtime


def form(config, action, **values):
    return {k: [str(v)] for k, v in {"csrf": config["csrf"], "action": action, **values}.items()}


def start(context, step=0):
    folder, config, runtime = context
    c.mutate(
        folder,
        config,
        form(
            config, "start", step=step, independent="yes", familiarity="used", seen_cases="unsure"
        ),
        runtime,
    )


def answer(config, step=0, **extra):
    return form(
        config,
        "answer",
        step=step,
        status="unsure",
        conclusion="Explicit control unknown",
        evidence_found="no",
        evidence_location="",
        evidence_excerpt="",
        evidence_reason="",
        missing_reason="Explicit test-only source missing",
        next_action="Explicit control inspect source",
        help_count=0,
        used_ai="no",
        interrupted="no",
        deviation_note="",
        **extra,
    )


def reach_ai(context):
    folder, config, runtime = context
    start(context)
    c.mutate(folder, config, answer(config), runtime)
    start(context, 1)


def test_precise_field_errors_keep_draft_and_do_not_append_answer(context):
    folder, config, runtime = context
    start(context)
    data = {
        **answer(config),
        "evidence_found": ["yes"],
        "used_ai": ["yes"],
        "deviation_note": ["无"],
    }
    with pytest.raises(c.FormError) as error:
        c.mutate(folder, config, data, runtime)
    assert {"evidence_location", "evidence_excerpt", "evidence_reason", "deviation_note"} <= set(
        error.value.fields
    )
    page = c.render(
        config,
        c.history(folder, config),
        "/trial/test",
        str(error.value),
        draft=data,
        field_errors=error.value.fields,
    ).decode()
    assert 'aria-invalid="true" aria-describedby="error-evidence_location"' in page
    assert 'href="#e4-evidence_excerpt"' in page
    assert "Explicit control unknown" in page
    assert len(c.history(folder, config)) == 1 and not runtime.sent


def test_early_exit_preserves_partial_records_without_fabricating_answer_or_seal(context):
    folder, config, runtime = context
    start(context)
    c.mutate(folder, config, answer(config), runtime)
    start(context, 1)
    before = (folder / "events.jsonl").read_bytes()
    c.mutate(
        folder,
        config,
        form(
            config,
            "withdraw",
            stop_confirmation="yes",
            stop_reason="Explicit control, cannot understand",
        ),
        runtime,
    )
    rows = c.history(folder, config)
    assert (folder / "events.jsonl").read_bytes().startswith(before)
    assert rows[-1]["kind"] == "withdraw" and rows[-1]["data"]["answers"] == 1
    assert not any(r["kind"] == "seal" for r in rows)
    assert "本轮已提前结束" in c.render(config, rows, "/trial/test").decode()
    assert "发送给实验助手" not in c.render(config, rows, "/trial/test").decode()
    with pytest.raises(ValueError):
        c.mutate(folder, config, answer(config, 1), runtime)
    assert not runtime.sent


def test_empty_get_does_not_start_or_prefill_and_original_phase_a_is_not_touched(context):
    folder, config, runtime = context
    assert not (folder / "events.jsonl").exists()
    page = c.render(config, [], "/trial/test").decode()
    assert "T13-N003" not in page and "<textarea" not in page
    assert c.prepare(runtime, "P01", 18037) == (folder, config)
    with pytest.raises(ValueError):
        c.prepare(runtime, "P01", 18038)
    start(context)
    page = c.render(config, c.history(folder, config), "/trial/test").decode()
    assert 'name="conclusion"' in page and ">请选择</option>" in page
    assert "发送给实验助手" not in page and "不能算正确" not in page
    assert not runtime.sent


def test_guide_condition_rejects_ai_and_answers_seal_append_only(context):
    folder, config, runtime = context
    start(context)
    with pytest.raises(ValueError):
        c.mutate(
            folder,
            config,
            form(config, "ask", step=0, question="control", request_id=uuid4()),
            runtime,
        )
    assert not runtime.sent
    for step in range(4):
        if step:
            start(context, step)
        c.mutate(folder, config, answer(config, step), runtime)
        saved = (folder / "events.jsonl").read_bytes()
        c.mutate(folder, config, answer(config, step), runtime)
        assert (folder / "events.jsonl").read_bytes() == saved
    c.mutate(
        folder,
        config,
        form(
            config,
            "seal",
            attestation="independent",
            feedback="Explicit mechanical test, not human feedback",
        ),
        runtime,
    )
    saved = (folder / "events.jsonl").read_bytes()
    assert len(c.history(folder, config)) == 9
    assert all(
        r["data"]["correctness"] == "pending_human_evidence_review"
        for r in c.history(folder, config)
        if r["kind"] == "answer"
    )
    with pytest.raises(ValueError):
        c.mutate(folder, config, answer(config), runtime)
    assert (folder / "events.jsonl").read_bytes() == saved


def test_explicit_ai_send_preserves_receipts_and_duplicate_send_has_no_second_call(context):
    folder, config, runtime = context
    reach_ai(context)
    rid = c.request_id(config, c.history(folder, config), 1)
    payload = form(config, "ask", step=1, question="<script>control</script>", request_id=rid)
    c.mutate(folder, config, payload, runtime)
    saved = (folder / "events.jsonl").read_bytes()
    c.mutate(folder, config, payload, runtime)
    c.mutate(folder, config, form(config, "receipt", step=1, request_id=rid), runtime)
    assert (folder / "events.jsonl").read_bytes() == saved
    assert len(runtime.sent) == 1 and not runtime.queried
    page = c.render(config, c.history(folder, config), "/trial/control").decode()
    assert "&lt;script&gt;" in page and "<script>control" not in page
    assert "本题会话上限约¥0.67" in page and "原回执、来源与调用明细" in page
    assert ">查看所选用例说明 ↗</a>" in page
    assert 'href="javascript:' not in page


def test_unknown_question_queries_only_original_and_does_not_duplicate_unknown_records(context):
    folder, config, runtime = context
    reach_ai(context)
    runtime.fail = True
    rid = c.request_id(config, c.history(folder, config), 1)
    c.mutate(
        folder,
        config,
        form(config, "ask", step=1, question="Explicit control", request_id=rid),
        runtime,
    )
    before = (folder / "events.jsonl").read_bytes()
    c.mutate(folder, config, form(config, "receipt", step=1, request_id=rid), runtime)
    assert (folder / "events.jsonl").read_bytes() == before
    assert len(runtime.sent) == 1 and runtime.queried == [rid]
    with pytest.raises(ValueError):
        c.mutate(
            folder,
            config,
            form(config, "ask", step=1, question="Different control", request_id=uuid4()),
            runtime,
        )
    assert len(runtime.sent) == 1
    assert "结果未知" in c.render(config, c.history(folder, config), "/trial/control").decode()


def test_request_rejects_other_entry_binding(context):
    folder, config, _ = context
    start(context)
    other = deepcopy(config)
    other["participant"] = "P02"
    with pytest.raises(ValueError):
        c.history(folder, other)


def test_http_origin_drift_input_preservation_and_expiry(context):
    folder, config, runtime = context
    server = HTTPServer(("127.0.0.1", 0), c.handler(folder, config, runtime))
    config["port"] = server.server_port
    server.RequestHandlerClass = c.handler(folder, config, runtime)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    endpoint = "/trial/" + config["token"]
    try:
        with httpx.Client(base_url=origin, trust_env=False) as client:
            assert client.get(endpoint).status_code == 200
            assert not (folder / "events.jsonl").exists()
            assert client.post(endpoint, data={"action": "start"}).status_code == 403
            start(context)
            bad = {k: v[0] for k, v in answer(config).items()}
            bad["evidence_found"] = "yes"
            bad["conclusion"] = "Preserve my <script>draft</script>"
            response = client.post(endpoint, data=bad, headers={"Origin": origin})
            assert response.status_code == 409 and "&lt;script&gt;draft" in response.text
            assert "<script>draft" not in response.text and 'value="0"' in response.text
            runtime.drift = True
            assert client.get(endpoint).status_code == 409
            runtime.drift = False
            config["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            assert client.get(endpoint).status_code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
