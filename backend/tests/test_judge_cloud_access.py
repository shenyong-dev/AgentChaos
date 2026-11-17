"""Owned SQL/Redis and gateway doubles only; never independent human evidence."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from agent_test_support import OfflineGateway
from langchain_core.messages import AIMessage
from test_cloud_budget import policy as policy  # noqa: F401
from test_judging import lab as lab  # noqa: F401
from test_judging import label_all

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core import cloud, cloud_init
from app.judging import service as module
from app.judging.budget import AUTHORIZATION, PROTOCOL_AUTHORIZATION, JudgeBudget, authorization
from app.judging.engine import manifest
from app.judging.service import COMPATIBLE_MAINTENANCE_SOURCES, JudgeService
from app.schemas.judging import RequestID, SealLabels


def test_authorization_accepts_cloud_but_not_old_open_approval_for_t17b(policy):
    settings, _ = policy
    assert authorization(settings, "T17B-v2") is False
    old = settings.model_copy(update={"judge_budget_authorization_id": AUTHORIZATION})
    with pytest.raises(AgentError) as exc:
        authorization(old, "T17B-v2")
    assert exc.value.code == "judge_task_authorization"
    finite = settings.model_copy(
        update={
            "judge_budget_authorization_id": PROTOCOL_AUTHORIZATION,
            "judge_budget_unlimited": False,
            "judge_budget_usd": 0.50,
        }
    )
    assert authorization(finite, "T17B-v2") is True
    with pytest.raises(AgentError):
        authorization(settings.model_copy(update={"cloud_approval_sha256": "0" * 64}), "T17B")


def test_compatibility_is_exact_and_never_accepts_prompt_model_or_unlisted_source_changes(policy):
    settings, _ = policy
    svc = JudgeService(None, None, settings)
    current = manifest(settings)
    previous = deepcopy(current)
    previous["sources"].update(COMPATIBLE_MAINTENANCE_SOURCES)
    assert svc._protocol_compatible(previous, current)
    for field in ("model", "prompt_hash", "schema_hash", "gateway_hash", "max_output_tokens"):
        changed = {**previous, field: "changed"}
        assert not svc._protocol_compatible(changed, current)
    for field in ("protocol.py", "engine.py", "budget.py"):
        changed = deepcopy(previous)
        changed["sources"][field] = "f" * 64
        assert not svc._protocol_compatible(changed, current)
    changed = deepcopy(current)
    changed["sources"]["service.py"] = "0" * 64
    assert not svc._protocol_compatible(previous, changed)
    local = JudgeService(None, None, settings.model_copy(update={"app_env": "development"}))
    assert not local._protocol_compatible(previous, current)


@pytest.fixture
def owned(lab, policy, monkeypatch):
    engine, r, _ = lab
    settings, marker = policy
    settings = settings.model_copy(update={"deepseek_api_key": "offline-synthetic-key"})
    # Use a SecretStr through validation rather than model_copy for the model key.
    settings = type(settings).model_validate(settings.model_dump())
    cloud_init.initialize(r, settings, marker)
    monkeypatch.setattr(cloud, "require_capacity", lambda _: None)
    gateway = OfflineGateway()
    monkeypatch.setattr(module, "ModelGateway", lambda *a, **k: gateway)
    yield JudgeService(engine, r, settings, dataset_task="T17B"), gateway
    keys = list(r.scan_iter(f"ac:cloud:{settings.cloud_deployment_id}:*"))
    if keys:
        r.delete(*keys)


def sealed(svc, monkeypatch, previous=False):
    current = manifest(svc.settings)
    old = deepcopy(current)
    old["sources"].update(COMPATIBLE_MAINTENANCE_SOURCES)
    with monkeypatch.context() as patch:
        if previous:
            patch.setattr(module, "manifest", lambda _: old)
        batch = svc.create(1, RequestID(request_id=uuid4()))
        label_all(svc, batch)
        svc.seal(
            2,
            batch["batch_id"],
            SealLabels(
                request_id=uuid4(),
                dataset_hash=batch["frozen"]["dataset"]["dataset_hash"],
                independent_from_judge=True,
            ),
        )
    return batch["batch_id"]


def test_readiness_is_read_only_and_compatibility_preserves_originals(owned, monkeypatch):
    svc, gateway = owned
    bid = sealed(svc, monkeypatch, previous=True)
    before = svc.read(1, bid)
    root = cloud.root_key(svc.settings, "judge")
    ledger = svc.redis.hgetall(root)
    for _ in range(2):
        current = svc.read(1, bid)
        assert current["evaluation_access"]["allowed"]
        assert current["evidence"] == before["evidence"]
        assert svc.redis.hgetall(root) == ledger
    assert not gateway.calls
    gateway.queue.append(AIMessage(content=json.dumps({})))
    request = RequestID(request_id=uuid4())
    result = svc.evaluate(1, bid, "B001", request)
    assert result["manifest_hash"] == canonical_hash(before["seal"]["manifest"])
    assert result["implementation_manifest_hash"] == canonical_hash(manifest(svc.settings))
    assert len(gateway.calls) == 1
    assert svc.evaluate(1, bid, "B001", request) == result
    after = svc.read(1, bid)
    assert after["labels"] == before["labels"] and after["seal"] == before["seal"]
    assert after["frozen"] == before["frozen"]
    assert sum(x["phase"] == "compatibility" for x in after["evidence"]) == 1


def test_missing_ledger_blocks_direct_post_without_attempt_or_reset(owned, monkeypatch):
    svc, gateway = owned
    bid = sealed(svc, monkeypatch)
    before = svc.read(1, bid)["evidence"]
    root = cloud.root_key(svc.settings, "judge")
    svc.redis.delete(root)
    access = svc.read(1, bid)["evaluation_access"]
    assert not access["allowed"] and access["code"] == "judge_ledger_unavailable"
    with pytest.raises(AgentError):
        svc.evaluate(1, bid, "B001", RequestID(request_id=uuid4()))
    assert not svc.redis.exists(root) and not gateway.calls
    assert svc.read(1, bid)["evidence"] == before


def test_unconfirmed_attempt_pauses_batch_and_completed_samples_are_not_reissued(
    owned, monkeypatch
):
    svc, gateway = owned
    bid = sealed(svc, monkeypatch)
    svc._append(1, bid, "judge_call", {"control": True}, "started", sample_id="B001")
    access = svc.read(1, bid)["evaluation_access"]
    assert not access["allowed"] and access["unconfirmed_ids"] == ["B001"]
    assert access["sample_states"]["B001"] == "unconfirmed"
    for sample in ("B001", "B002"):
        with pytest.raises(AgentError):
            svc.evaluate(1, bid, sample, RequestID(request_id=uuid4()))
    assert not gateway.calls


def test_role_scope_version_drift_and_disabled_switch_are_rechecked(owned, monkeypatch):
    svc, gateway = owned
    bid = sealed(svc, monkeypatch)
    assert not svc.read(2, bid)["evaluation_access"]["allowed"]
    with pytest.raises(AgentError):
        svc.read(3, bid)
    with pytest.raises(AgentError):
        svc.evaluate(2, bid, "B001", RequestID(request_id=uuid4()))
    svc.settings = svc.settings.model_copy(update={"judge_allow_paid_model_calls": False})
    assert svc.read(1, bid)["evaluation_access"]["code"] == "judge_not_authorized"
    with pytest.raises(AgentError):
        svc.evaluate(1, bid, "B001", RequestID(request_id=uuid4()))
    svc.settings = svc.settings.model_copy(update={"judge_allow_paid_model_calls": True})
    changed = manifest(svc.settings)
    changed["prompt_hash"] = "0" * 64
    monkeypatch.setattr(module, "manifest", lambda _: changed)
    assert svc.read(1, bid)["evaluation_access"]["code"] == "judge_version_changed"
    assert not gateway.calls


def test_frozen_batch_cap_is_checked_without_issuing_fresh_allowance(owned):
    svc, _ = owned
    budget = JudgeBudget(
        svc.redis,
        svc.settings,
        uuid4().hex,
        None,
        limits={"calls": 12, "tokens": 1_000_000, "microusd": 500_000},
    )
    svc.redis.hset(budget.calls_key, mapping={"calls_started": 12})
    snapshot = svc.redis.hgetall(budget.calls_key)
    with pytest.raises(AgentError) as exc:
        budget.check_ready()
    assert exc.value.code == "judge_budget_unavailable"
    assert svc.redis.hgetall(budget.calls_key) == snapshot
    root = cloud.root_key(svc.settings, "judge")
    before = svc.redis.hgetall(root)
    with pytest.raises(AgentError):
        budget.reserve(8, 512)
    assert svc.redis.hgetall(root) == before
