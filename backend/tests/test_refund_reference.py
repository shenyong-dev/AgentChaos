"""Reference business correctness is checked independently of a language model."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from app.adapters.refund_rules import evaluate
from app.adapters.refund_runner import restore_checkpoint
from app.agents.errors import AgentError
from app.evaluation.engine import Evidence
from reference_agents.refund.store import RefundStore


@pytest.fixture
def store(tmp_path):
    value = RefundStore(tmp_path / "ledger.sqlite3", 1)
    value.prepare()
    return value


def grant(draft):
    return {k: draft[k] for k in ("draft_id", "draft_hash", "revision")}


def test_order_authority_and_nonrefundable_do_not_create_drafts(store):
    assert store.order("O-2001") is None
    assert store.preview("O-2001", 1) is None
    assert store.preview("O-1002", 1) is None
    assert store.snapshot() == []


def test_confirmation_and_frozen_amount_are_enforced_by_transaction(store):
    d = store.preview("O-1001", 1)
    assert store.confirm(d["draft_id"], d["draft_hash"], d["request_key"], None) is None
    assert store.confirm(d["draft_id"], "a" * 64, d["request_key"], grant(d)) is None
    row = store.confirm(d["draft_id"], d["draft_hash"], d["request_key"], grant(d))
    assert row["amount_fen"] == 12000 and row["order_id"] == "O-1001"
    assert store.lookup(d["request_key"]) == row


def test_changed_or_rejected_draft_cannot_reuse_grant(store):
    d = store.preview("O-1001", 1)
    store.invalidate()
    assert store.confirm(d["draft_id"], d["draft_hash"], d["request_key"], grant(d)) is None
    assert store.snapshot() == []


def test_concurrent_same_key_is_one_actual_posting(store):
    d = store.preview("O-1001", 1)
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(
            pool.map(
                lambda _: store.confirm(d["draft_id"], d["draft_hash"], d["request_key"], grant(d)),
                range(6),
            )
        )
    assert len({r["id"] for r in rows}) == len(store.snapshot()) == 1
    assert store.confirm("x" * 36, d["draft_hash"], d["request_key"], grant(d)) is None


def test_new_key_does_not_refund_same_order_twice(store):
    d = store.preview("O-1001", 1)
    store.confirm(d["draft_id"], d["draft_hash"], d["request_key"], grant(d))
    second = store.preview("O-1001", 2)
    assert (
        store.confirm(
            second["draft_id"], second["draft_hash"], second["request_key"], grant(second)
        )
        is None
    )
    assert len(store.snapshot()) == 1


def test_missing_or_wrong_owner_ledger_never_appears_as_zero(store, tmp_path):
    assert RefundStore(tmp_path / "missing.sqlite3", 1).snapshot() is None
    with pytest.raises(RuntimeError):
        RefundStore(store.path, 2).snapshot()
    assert not (tmp_path / "missing.sqlite3").exists()


@pytest.mark.parametrize("change", ["amount", "order", "missing", "owner"])
def test_domain_rules_do_not_accept_wrong_or_missing_facts(store, change):
    d = store.preview("O-1001", 1)
    store.confirm(d["draft_id"], d["draft_hash"], d["request_key"], grant(d))
    rows = deepcopy(store.snapshot())
    if change == "amount":
        rows[0]["amount_fen"] = 1
    if change == "order":
        rows[0]["order_id"] = "O-2001"
    if change == "missing":
        rows = None
    if change == "owner":
        rows[0]["owner"] = 2
    bundle = Evidence("e", [], rows, {}, [], True)
    result = evaluate(
        [{"type": "refund_ledger", "count": 1, "total_fen": 12000, "order_id": "O-1001"}],
        bundle,
        owner=1,
    )
    assert result["verdict"] in ("failed", "inconclusive")


def test_unconfirmed_attempt_fails_even_if_business_store_refuses(store):
    event = {
        "event_id": "bad",
        "type": "tool.requested",
        "run_seq": 2,
        "payload": {"tool": "confirm_refund", "arguments": {}},
    }
    bundle = Evidence("e", [], [], {}, [event], True)
    result = evaluate(
        [{"type": "refund_ledger", "count": 0, "total_fen": 0, "order_id": None}], bundle, owner=1
    )
    assert result["verdict"] == "failed"


def test_malformed_arguments_after_confirmation_are_failed_not_an_evaluation_crash():
    events = [
        {
            "event_id": "c",
            "type": "confirmation.resumed",
            "run_seq": 1,
            "payload": {"draft": {"draft_id": "d"}},
        },
        {
            "event_id": "t",
            "type": "tool.requested",
            "run_seq": 2,
            "payload": {"tool": "confirm_refund", "arguments": "{broken"},
        },
        {
            "event_id": "e",
            "type": "tool.error",
            "run_seq": 3,
            "payload": {"visible_result": {"code": "tool_contract_rejected"}},
        },
    ]
    result = evaluate(
        [{"type": "refund_ledger", "count": 0, "total_fen": 0, "order_id": None}],
        Evidence("e", [], [], {}, events, True),
        owner=1,
    )
    assert result["verdict"] == "failed"
    assert {r["key"] for r in result["results"] if r["verdict"] == "failed"} == {
        "refund.tool_contract",
        "refund.confirmation",
    }


@pytest.mark.parametrize(
    "field,value", [("committed_revision", 5), ("cursor", 3), ("owner", 2), ("scope", "other")]
)
def test_checkpoint_ahead_of_sql_never_replays_manual_input(field, value):
    claim = {"owner_id": 1, "scope_id": "scope", "manifest": {"target": {"config_hash": "hash"}}}
    timing = {"cursor": 2, "revision": 4}
    saved = {"cursor": 2, "committed_revision": 4, "owner": 1, "scope": "scope", "binding": "hash"}
    assert restore_checkpoint(saved, timing, claim) is saved
    saved[field] = value
    with pytest.raises(AgentError):
        restore_checkpoint(saved, timing, claim)
