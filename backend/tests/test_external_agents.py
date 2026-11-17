"""Real local HTTP protocol controls; no models, provider credentials or business writes."""

import json
import threading
import time
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpcore
import pytest
from pydantic import ValidationError

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import Settings
from app.external.adapter import ExternalAdapter
from app.external.registry import address, entries, entry_for
from app.external.runner import lookup, request, validate_result
from app.external.schema import Card, Entry
from app.external.transport import Client, DeadlineStream, PinnedBackend
from examples.external_agent.server import CARD, Store, make_server

TOKEN = "p06-synthetic-test-token-never-real"


def settings_for(path):
    return Settings(
        _env_file=None,
        app_env="test",
        external_agent_registry_path=str(path),
        external_agent_calls_enabled=True,
    )


def entry_value(port):
    return {
        "id": "example",
        "endpoint": f"http://127.0.0.1:{port}/agentchaos/v1",
        "pinned_ip": "127.0.0.1",
        "network_zone": "approved_private",
        "credential_env": "AGENTCHAOS_EXTERNAL_TEST_TOKEN",
        "card": CARD,
        "enabled": True,
        "timeout_seconds": 2,
        "cost_upper_microusd": 0,
        "token_upper": 8192,
        "author_relation": "project_example",
    }


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCHAOS_EXTERNAL_TEST_TOKEN", TOKEN)
    server, control = make_server(tmp_path / "remote.sqlite3", TOKEN)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    value = entry_value(server.server_port)
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"schema_version": "agentchaos-external-registry-1", "targets": [value]}),
        encoding="utf-8",
    )
    try:
        yield entry_for(settings_for(path), "external_http:example"), settings_for(path), control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def binding():
    return {"tenant": "a" * 64, "scope": "test:external:" + uuid4().hex, "session_id": str(uuid4())}


def send_data(text="你好", revision=0):
    return {
        "text": text,
        "expected_revision": revision,
        "limits": {
            "max_model_calls": 1,
            "max_tokens": 8192,
            "max_cost_microusd": 0,
            "timeout_seconds": 2,
        },
    }


def test_real_http_capability_prepare_send_lookup_and_same_key_reuse(remote):
    entry, settings, control = remote
    client, scope = Client(entry, settings), binding()
    assert client.call(request(entry, scope, "capabilities", uuid4(), {})) == CARD
    prepare = request(entry, scope, "prepare", uuid4(), {})
    assert client.call(prepare) == client.call(prepare)
    original = request(entry, scope, "execute", uuid4(), send_data())
    response = validate_result("execute", client.call(original), original)
    assert client.call(original) == response
    assert lookup(client, original) == response
    assert response["assistant_text"] == "收到：你好"
    assert (
        control["operations"].count("execute") == 2
    )  # duplicate HTTP delivery, one remote revision
    next_request = request(entry, scope, "execute", uuid4(), send_data("下一句", 1))
    assert client.call(next_request)["revision"] == 2
    changed = deepcopy(original)
    changed["data"]["text"] = "同键换内容"
    with pytest.raises(AgentError):
        client.call(changed)


def test_response_loss_is_queryable_without_resending_and_survives_store_reopen(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AGENTCHAOS_EXTERNAL_TEST_TOKEN", TOKEN)
    ledger = tmp_path / "ledger.sqlite3"
    server, control = make_server(ledger, TOKEN, drop_first_execute=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    entry = Entry.model_validate(entry_value(server.server_port))
    settings = Settings(_env_file=None, app_env="test", external_agent_calls_enabled=True)
    client, scope = Client(entry, settings), binding()
    try:
        client.call(request(entry, scope, "prepare", uuid4(), {}))
        original = request(entry, scope, "execute", uuid4(), send_data())
        with pytest.raises(AgentError, match="外部调用未取得可信回执"):
            client.call(original)
        result = lookup(client, original)
        assert result["revision"] == 1 and control["operations"].count("execute") == 1
        reopened = Store(ledger)
        assert (
            reopened.handle(
                {
                    **original,
                    "operation": "lookup",
                    "data": {
                        "original_operation": "execute",
                        "input_hash": canonical_hash(original["data"]),
                    },
                }
            )["result"]
            == result
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_wrong_owner_scope_session_and_key_never_reuse_other_receipt(remote):
    entry, settings, _ = remote
    scope = binding()
    client = Client(entry, settings)
    client.call(request(entry, scope, "prepare", uuid4(), {}))
    original = request(entry, scope, "execute", uuid4(), send_data())
    client.call(original)
    for field, value in [
        ("tenant", "b" * 64),
        ("scope", "test:other"),
        ("session_id", str(uuid4())),
    ]:
        changed = deepcopy(original)
        changed["binding"][field] = value
        with pytest.raises(AgentError):
            lookup(client, changed)


def test_cancel_before_delivery_disables_original_input(remote):
    entry, settings, _ = remote
    client, scope = Client(entry, settings), binding()
    client.call(request(entry, scope, "prepare", uuid4(), {}))
    pending = request(entry, scope, "execute", uuid4(), send_data())
    cancel = request(
        entry,
        scope,
        "cancel",
        uuid4(),
        {
            "original_request_id": pending["request_id"],
            "input_hash": canonical_hash(pending["data"]),
        },
    )
    assert client.call(cancel) == {"status": "confirmed"}
    stopped = client.call(pending)
    assert stopped["state"] == "cancelled" and not stopped["assistant_text"]
    assert lookup(client, pending) == stopped


@pytest.mark.parametrize(
    "endpoint,ip,zone,production",
    [
        ("https://service.example/agent", "127.0.0.1", "public", False),
        ("https://service.example/agent", "169.254.169.254", "approved_private", False),
        ("https://service.example/agent", "::ffff:127.0.0.1", "approved_private", False),
        ("http://service.example/agent", "8.8.8.8", "public", False),
        ("https://user:password@service.example/agent", "8.8.8.8", "public", False),
        ("https://service.example/agent?url=evil", "8.8.8.8", "public", False),
        ("http://127.0.0.1/agent", "127.0.0.1", "approved_private", True),
    ],
)
def test_address_policy_refuses_unregistered_network_classes(endpoint, ip, zone, production):
    value = entry_value(1234)
    value.update(endpoint=endpoint, pinned_ip=ip, network_zone=zone)
    with pytest.raises(AgentError):
        address(
            Entry.model_validate(value),
            SimpleNamespace(app_env="production" if production else "test"),
        )


def test_pinned_backend_uses_registered_ip_without_resolving_hostname(monkeypatch):
    calls = []
    backend = PinnedBackend("agent.example", 443, "8.8.8.8", time.monotonic() + 2)
    monkeypatch.setattr(
        backend.backend, "connect_tcp", lambda *args: calls.append(args) or object()
    )
    assert isinstance(backend.connect_tcp("agent.example", 443, 1), DeadlineStream)
    assert calls[0][0] == "8.8.8.8"
    with pytest.raises(httpcore.ConnectError):
        backend.connect_tcp("other.example", 443, 1)
    assert len(calls) == 1


def test_deadline_stream_limits_each_io_and_tls_keeps_original_hostname():
    calls = []

    class Fake:
        def read(self, max_bytes, timeout):
            calls.append((max_bytes, timeout))
            return b"ok"

        def start_tls(self, ssl_context, server_hostname, timeout):
            calls.append(server_hostname)
            return self

    stream = DeadlineStream(Fake(), time.monotonic() + 0.5)
    assert stream.read(8, timeout=20) == b"ok" and 0 < calls[0][1] <= 0.5
    stream.start_tls(None, "agent.example", 10)
    assert calls[1] == "agent.example"
    with pytest.raises(httpcore.ReadTimeout):
        DeadlineStream(Fake(), time.monotonic() - 1).read(8, 20)


def test_disabled_calls_and_bad_auth_do_not_start_messages(remote, monkeypatch):
    entry, settings, control = remote
    with pytest.raises(AgentError):
        Client(entry, settings.model_copy(update={"external_agent_calls_enabled": False})).call(
            request(entry, binding(), "execute", uuid4(), send_data())
        )
    monkeypatch.setenv("AGENTCHAOS_EXTERNAL_TEST_TOKEN", "different-synthetic-token")
    with pytest.raises(AgentError):
        Client(entry, settings).call(request(entry, binding(), "capabilities", uuid4(), {}))
    assert control["operations"] == []


def test_registry_and_boolean_contract_do_not_accept_extra_endpoint_or_fake_capability(tmp_path):
    value = entry_value(1234)
    value["card"] = {**CARD, "side_effects": 0}
    with pytest.raises(ValidationError):
        Entry.model_validate(value)
    value["card"] = {**CARD, "side_effects": True}
    with pytest.raises(ValidationError):
        Card.model_validate(value["card"])
    path = tmp_path / "registry.json"
    path.write_text(
        '{"schema_version":"agentchaos-external-registry-1","targets":[],"targets":[]}',
        encoding="utf-8",
    )
    with pytest.raises(AgentError):
        entries(settings_for(path))


def test_manifest_has_no_credential_or_endpoint_and_no_booking_metrics(remote):
    entry, settings, _ = remote
    manifest = ExternalAdapter(None, settings, "external_http:example").manifest()
    serialized = json.dumps(manifest)
    assert TOKEN not in serialized and entry.endpoint not in serialized
    assert manifest["capabilities"]["business_facts"] is False
    assert manifest["capabilities"]["ai_judge"] is False
    assert manifest["retrieval"]["mode"] == "not_applicable"


@pytest.mark.parametrize(
    "fault", ["redirect", "bad_json", "duplicate", "too_large", "wrong_binding", "compressed"]
)
def test_untrusted_responses_fail_without_redirect_or_retry(remote, monkeypatch, fault):
    entry, settings, _ = remote
    original = request(entry, binding(), "execute", uuid4(), send_data())
    response = {**original, "data": {"test": "value"}}
    if fault == "wrong_binding":
        response["request_id"] = str(uuid4())
    raw = json.dumps(response).encode()
    if fault == "bad_json":
        raw = b"not-json"
    if fault == "duplicate":
        raw = b'{"protocol":"old","protocol":"new"}'
    if fault == "too_large":
        raw = b"x" * 65537
    calls = []

    class Response:
        status = 302 if fault == "redirect" else 200
        headers = [
            (b"content-type", b"application/json"),
            (b"content-encoding", b"gzip" if fault == "compressed" else b"identity"),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_stream(self):
            yield raw

    class Pool:
        def __init__(self, **kwargs):
            assert kwargs["retries"] == 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            calls.append(args)
            return Response()

    monkeypatch.setattr(httpcore, "ConnectionPool", Pool)
    with pytest.raises(AgentError):
        Client(entry, settings).call(original)
    assert len(calls) == 1


def test_reflected_service_credential_is_removed_before_return_or_persistence(remote, monkeypatch):
    entry, settings, _ = remote
    original = request(entry, binding(), "capabilities", uuid4(), {})
    raw = json.dumps({**original, "data": {"remote_text": "echo " + TOKEN}}).encode()

    class Response:
        status = 200
        headers = [(b"content-type", b"application/json")]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_stream(self):
            yield raw

    class Pool:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(httpcore, "ConnectionPool", Pool)
    result = Client(entry, settings).call(original)
    assert TOKEN not in json.dumps(result) and result["remote_text"] == "echo [REDACTED]"
