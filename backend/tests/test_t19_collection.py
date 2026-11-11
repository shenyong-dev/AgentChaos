"""Explicit mechanical collector controls; never represent human trial answers."""

import json
from datetime import UTC, datetime, timedelta
from http.server import HTTPServer
from threading import Thread

import httpx
import pytest

from app.agents.versions import canonical_hash
from benchmarks.t19 import collector as c


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ROOT", tmp_path / "participants")
    protocol = tmp_path / "protocol.json"
    protocol.write_text(
        json.dumps(
            {
                "schema": "explicit-offline-recorder-control",
                "instructions": "Explicit mechanical test, not independent human evidence",
                "platform_url": "http://127.0.0.1:5173",
                "tasks": [
                    {
                        "id": "X01",
                        "title": "control one",
                        "instruction": "test instruction one",
                        "path": "/workspace",
                    },
                    {
                        "id": "X02",
                        "title": "control two",
                        "instruction": "test instruction two",
                        "path": "/targets",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(c, "PROTOCOL", protocol)
    monkeypatch.setattr(c, "source_manifest", lambda: {"explicit-control": "1" * 64})
    folder, config = c.prepare("P01", 18035)
    return folder, config


def form(config, action, **kwargs):
    return {
        key: [str(value)]
        for key, value in {"csrf": config["csrf"], "action": action, **kwargs}.items()
    }


def begin(folder, config, step=0):
    c.mutate(folder, config, form(config, "start", step=step, familiarity="new", independent="yes"))


def answer(config, step=0, **kwargs):
    return form(
        config,
        "answer",
        **{
            "step": step,
            "status": "unsure",
            "answer": "Explicit test-only mechanical answer <script>x</script>",
            "help_count": 0,
            "used_ai": "no",
            "interrupted": "no",
            "note": "",
            **kwargs,
        },
    )


def test_blank_forms_no_start_clock_on_get_and_resume(context):
    folder, config = context
    assert c.history(folder) == []
    initial = c.render(config, [], "/trial/control").decode()
    assert "test instruction one" not in initial and "<textarea" not in initial
    assert not (folder / "events.jsonl").exists()
    begin(folder, config)
    shown = c.render(config, c.history(folder), "/trial/control").decode()
    assert 'name="answer" required' in shown and "test instruction one" in shown
    assert "Explicit test-only mechanical answer" not in shown
    assert ">请选择</option>" in shown and "not independent human evidence" not in shown
    assert c.prepare("P01", 18035)[1] == config
    with pytest.raises(ValueError):
        c.prepare("P01", 18036)


def test_sequential_answers_retries_and_final_append_only_seal(context):
    folder, config = context
    begin(folder, config)
    first_start = (folder / "events.jsonl").read_bytes()
    begin(folder, config)
    assert (folder / "events.jsonl").read_bytes() == first_start
    c.mutate(folder, config, answer(config))
    first = (folder / "events.jsonl").read_bytes()
    c.mutate(folder, config, answer(config))
    assert (folder / "events.jsonl").read_bytes() == first
    with pytest.raises(ValueError):
        c.mutate(folder, config, answer(config, answer="Do not overwrite"))
    with pytest.raises(ValueError):
        c.mutate(folder, config, form(config, "seal", attestation="independent", feedback="early"))
    begin(folder, config, 1)
    c.mutate(folder, config, answer(config, 1, status="blocked"))
    c.mutate(
        folder, config, form(config, "seal", attestation="independent", feedback="explicit control")
    )
    records = c.history(folder)
    assert [r["kind"] for r in records] == ["start", "answer", "start", "answer", "seal"]
    assert all(r["protocol_hash"] == canonical_hash(config["protocol"]) for r in records)
    assert all(
        r["data"]["correctness"] == "pending_independent_review" and r["data"]["wall_seconds"] >= 0
        for r in records
        if r["kind"] == "answer"
    )
    sealed = (folder / "events.jsonl").read_bytes()
    with pytest.raises(ValueError):
        c.mutate(folder, config, answer(config))
    assert (folder / "events.jsonl").read_bytes() == sealed
    assert "已封存" in c.render(config, records, "/trial/control").decode()


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "invented"),
        ("answer", " "),
        ("help_count", -1),
        ("help_count", 21),
        ("used_ai", "maybe"),
        ("csrf", "wrong"),
        ("interrupted", "yes"),
        ("used_ai", "yes"),
        ("help_count", 1),
        ("step", 1),
    ],
)
def test_invalid_or_unexplained_inputs_do_not_append(context, field, value):
    folder, config = context
    begin(folder, config)
    original = (folder / "events.jsonl").read_bytes()
    payload = answer(config)
    payload[field] = [str(value)]
    with pytest.raises(ValueError):
        c.mutate(folder, config, payload)
    assert (folder / "events.jsonl").read_bytes() == original


def test_help_and_ai_contamination_are_preserved_not_scored_as_success(context):
    folder, config = context
    begin(folder, config)
    c.mutate(
        folder,
        config,
        answer(config, help_count=2, used_ai="yes", interrupted="yes", note="explicit control"),
    )
    payload = c.history(folder)[-1]["data"]
    assert payload["used_ai"] == "yes" and payload["help_count"] == 2
    assert payload["correctness"] == "pending_independent_review"
    assert payload["timing_boundary"].endswith("not active input time")


def test_missing_attestation_repeated_fields_and_tampering_fail_closed(context):
    folder, config = context
    payload = form(config, "start", step=0, familiarity="new", independent="no")
    with pytest.raises(ValueError):
        c.mutate(folder, config, payload)
    begin(folder, config)
    payload = answer(config)
    payload["answer"].append("second answer")
    with pytest.raises(ValueError):
        c.mutate(folder, config, payload)
    path = folder / "events.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace('"P01"', '"P02"'), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        c.history(folder)


def test_http_origin_token_body_guard_source_drift_and_expiry(context, monkeypatch):
    folder, config = context
    server = HTTPServer(("127.0.0.1", 0), c.handler(folder, config))
    config["port"] = server.server_port
    server.RequestHandlerClass = c.handler(folder, config)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    path = "/trial/" + config["token"]
    data = {
        key: values[0]
        for key, values in form(
            config, "start", step=0, familiarity="new", independent="yes"
        ).items()
    }
    try:
        with httpx.Client(
            base_url=base, follow_redirects=False, timeout=5, trust_env=False
        ) as client:
            assert client.get("/entry.json").status_code == 404
            assert client.get(path, headers={"Host": "forged.invalid"}).status_code == 404
            page = client.get(path)
            assert page.status_code == 200 and page.headers["Cache-Control"] == "no-store"
            assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
            assert (
                client.post(path, data=data, headers={"Origin": "http://evil.invalid"}).status_code
                == 403
            )
            assert client.post(path, data=data).status_code == 403
            assert client.post(path, data=data, headers={"Origin": "null"}).status_code == 403
            assert (
                client.post(
                    path, content=b"x" * (c.MAX_FORM_BYTES + 1), headers={"Origin": base}
                ).status_code
                == 409
            )
            assert client.post(path, data=data, headers={"Origin": base}).status_code == 303
            unicode_answer = {
                key: values[0]
                for key, values in answer(config, answer="😀" * 6000, note="😀" * 2000).items()
            }
            assert (
                client.post(path, data=unicode_answer, headers={"Origin": base}).status_code == 303
            )
            monkeypatch.setattr(c, "source_manifest", lambda: {"different-source": "2" * 64})
            assert client.get(path).status_code == 409
            original = (folder / "events.jsonl").read_bytes()
            assert client.post(path, data=data, headers={"Origin": base}).status_code == 409
            assert (folder / "events.jsonl").read_bytes() == original
            config["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            assert client.get(path).status_code == 410
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_process_lock_prevents_second_writer(context):
    folder, _ = context
    with c.process_lock(folder), pytest.raises(OSError), c.process_lock(folder):
        pytest.fail("Second writer should not acquire lock")
