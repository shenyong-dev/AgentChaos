"""T17B mechanical controls only; these do not supply calibration human labels."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from pydantic import SecretStr
from redis import Redis
from test_judging import answer

from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.core.config import Settings
from app.judging.budget import PROTOCOL_AUTHORIZATION, JudgeBudget
from app.judging.engine import interpret, manifest
from app.judging.protocol import messages, parse_response
from app.judging.service import load_dataset


def payload(evidence=None):
    return {
        "dialogue": [{"user": "只评价本次回复"}],
        "dialogue_scope": "All provided turns",
        "reply": "unknown",
        "evidence": evidence or [],
    }


@pytest.mark.parametrize("dimension", ["support", "explanation"])
def test_empty_citations_only_legal_for_insufficient_and_legacy_remains_strict(dimension):
    value = answer("insufficient", "insufficient")
    for key in ("support", "explanation"):
        value[key]["evidence_refs"] = []
    assert interpret(value, payload())["status"] == "inconclusive"
    assert (
        interpret(value, payload(), protocol_version="T12-v1")["reason"] == "judge_schema_invalid"
    )
    value[dimension]["label"] = "supported" if dimension == "support" else "reasonable"
    assert interpret(value, payload())["reason"] == "judge_invalid_citations"


@pytest.mark.parametrize("ref", ["sql", "invented-id", "", "reply "])
def test_absent_evidence_is_not_fabricated_by_parser(ref):
    value = answer()
    value["support"]["evidence_refs"] = [ref]
    assert interpret(value, payload())["reason"] == "judge_unknown_reference"


def test_actual_empty_sql_is_citable_but_null_sql_and_empty_dialogue_are_not():
    value = answer()
    value["support"]["evidence_refs"] = ["sql"]
    value["explanation"]["evidence_refs"] = ["reply"]
    assert interpret(value, payload([{"id": "sql", "content": []}]))["status"] == "passed"
    assert interpret(value, payload([{"id": "sql", "content": None}]))["status"] == "error"
    value["support"]["evidence_refs"] = ["dialogue"]
    empty = {**payload(), "dialogue": []}
    assert interpret(value, empty)["status"] == "error"


@pytest.mark.parametrize(
    "content",
    [
        " ",
        "{} trailing",
        '{"support":',
        '{"x":1,"x":2}',
        '{"nested":{"x":1,"x":2}}',
        '{"value":NaN}',
        '{"value":Infinity}',
    ],
)
def test_json_is_not_silently_repaired(content):
    with pytest.raises(ValueError):
        parse_response(content)


def test_review_flag_and_top_level_shape_are_strict():
    value = answer(confidence=0.5)
    value["support"]["evidence_refs"] = ["reply"]
    value["needs_review"] = False
    assert interpret(value, payload())["reason"] == "judge_contradictory_output"
    value = answer()
    value["confidence"] = 0.9
    assert interpret(value, payload())["reason"] == "judge_schema_invalid"
    assert interpret({"support": "supported"}, payload())["status"] == "error"
    assert interpret({}, payload(), protocol_version="unregistered")["status"] == "error"


def test_new_input_allowlist_and_concrete_prompt_examples_match_the_same_schema():
    protocol = manifest(Settings())
    observed = payload()
    request = messages(
        protocol, {**observed, "human_label": "PRIVATE_LABEL", "rule_verdict": "PRIVATE_RULE"}
    )
    assert json.loads(request[1].content) == {"view": "answer_only", "observed": observed}
    assert "PRIVATE_LABEL" not in request[1].content and "PRIVATE_RULE" not in request[1].content
    examples = [
        json.loads(line)
        for line in protocol["prompt"].splitlines()
        if line.startswith('{"support"')
    ]
    assert len(examples) == 2
    assert [interpret(v, observed)["status"] for v in examples] == ["passed", "inconclusive"]
    assert protocol["schema"]["properties"]["support"]["$ref"].endswith("SupportRatingV2")
    new = load_dataset("T17B")
    assert len(new["samples"]) == 12 and len({x["sample_hash"] for x in new["samples"]}) == 12
    assert sum(bool(x["payload"]["evidence"]) for x in new["samples"]) == 6
    assert not {x["sample_hash"] for x in new["samples"]} & {
        x["sample_hash"] for x in load_dataset()["samples"]
    }


@pytest.mark.parametrize("blank", [False, True])
def test_gateway_sends_and_audits_json_format_and_never_retries(monkeypatch, tmp_path, blank):
    options = []

    class Client:
        def __init__(self, **kwargs):
            options.append(kwargs)

        def stream(self, *_args, **_kwargs):
            yield AIMessageChunk(
                content=" " if blank else "{}",
                response_metadata={"finish_reason": "stop"},
                usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            )

    class Budget:
        def reserve(self, *_):
            return {"call_id": "mechanical", "upper_microusd": 100}

        def settle(self, *_):
            return 5

        def unknown(self, *_):
            pass

    monkeypatch.setattr("app.agents.gateway.ChatDeepSeek", Client)
    s = Settings().model_copy(update={"deepseek_api_key": SecretStr("mechanical-not-a-key")})
    gateway = ModelGateway(s, Budget(), role="judge", audit_directory=tmp_path)
    if blank:
        with pytest.raises(AgentError, match="裁判返回空白"):
            gateway.invoke([HumanMessage(content="json")], [])
    else:
        gateway.invoke([HumanMessage(content="json")], [])
    raw = json.loads((tmp_path / "mechanical.json").read_bytes())
    assert len(options) == 1 and options[0]["max_retries"] == 0
    assert (
        options[0]["response_format"]
        == raw["request"]["response_format"]
        == {"type": "json_object"}
    )
    assert raw["usage"]["total_tokens"] == 7


@pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="Real Redis only, no provider calls")
def test_finite_judge_authorization_is_shared_across_batches_under_concurrency():
    s = Settings().model_copy(
        update={
            "judge_allow_paid_model_calls": True,
            "judge_budget_unlimited": False,
            "judge_budget_authorization_id": PROTOCOL_AUTHORIZATION,
            "judge_budget_usd": 0.5,
        }
    )
    r = Redis.from_url(s.redis_url)
    prefix = "ac:t17b:test:" + uuid4().hex
    budgets = [JudgeBudget(r, s, str(uuid4()), lambda *_: None, prefix=prefix) for _ in range(20)]
    try:
        with pytest.raises(AgentError):
            budgets[0].reserve(100, 1536)
        assert not r.exists(budgets[0].key)
        r.hset(budgets[0].key, "limit", 500_000)

        def reserve(budget):
            try:
                return budget, budget.reserve(100, 1536)
            except AgentError as exc:
                assert exc.code == "judge_budget_unavailable"
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [x for x in pool.map(reserve, budgets) if x]
        assert len(results) == 12 and budgets[0].snapshot()["calls_started"] == 12
        for budget, ticket in results:
            budget.unknown(ticket)
            budget.settle(ticket, {"input_tokens": 10, "output_tokens": 2})
        assert budgets[0].snapshot()["held_upper"] == 0
        assert budgets[0].snapshot()["calls_unknown"] == 0
        closed = JudgeBudget(
            r,
            s.model_copy(update={"judge_allow_paid_model_calls": False}),
            "closed",
            lambda *_: None,
            prefix=prefix,
        )
        with pytest.raises(AgentError):
            closed.reserve(100, 1536)
    finally:
        owned = list(r.scan_iter(prefix + "*"))
        if owned:
            r.delete(*owned)
        r.close()
