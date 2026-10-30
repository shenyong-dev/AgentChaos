"""Disposable T17B twelve-item seal controls, never real independent labels."""

from uuid import uuid4

import pytest
from agent_test_support import OfflineGateway
from test_judging import lab as lab  # noqa:F401
from test_judging import label_all

from app.agents.errors import AgentError
from app.judging.service import JudgeService
from app.schemas.judging import RequestID, SealLabels


def sealed_control(lab):
    gateway = OfflineGateway()
    svc = JudgeService(*lab, dataset_task="T17B", gateway_factory=lambda *_: gateway)
    batch = svc.create(1, RequestID(request_id=uuid4()))
    label_all(svc, batch)  # Explicit mechanical test labels in this disposable database only.
    seal = svc.seal(
        2,
        batch["batch_id"],
        SealLabels(
            request_id=uuid4(),
            dataset_hash=batch["frozen"]["dataset"]["dataset_hash"],
            independent_from_judge=True,
        ),
    )
    return svc, gateway, batch, seal


def test_twelve_item_seal_is_complete_and_old_budget_cannot_pay_for_new_task(lab):
    svc, gateway, batch, seal = sealed_control(lab)
    assert len(seal["labels"]) == 12 and not gateway.calls
    real_boundary = JudgeService(*lab, dataset_task="T17B")
    with pytest.raises(AgentError) as exc:
        real_boundary.evaluate(1, batch["batch_id"], "B001", RequestID(request_id=uuid4()))
    assert exc.value.code == "judge_task_authorization"
    current = svc.read(1, batch["batch_id"])
    assert not current["results"] and len(current["labels"]) == 12
    with pytest.raises(AgentError):
        svc.read(3, batch["batch_id"])


def test_full_protocol_manifest_must_stay_frozen_before_sealing(lab):
    engine, redis, settings = lab
    svc = JudgeService(*lab, dataset_task="T17B")
    batch = svc.create(1, RequestID(request_id=uuid4()))
    label_all(svc, batch)
    changed = JudgeService(
        engine,
        redis,
        settings.model_copy(update={"judge_max_output_tokens": 1536}),
        dataset_task="T17B",
    )
    assert settings.judge_max_output_tokens != 1536
    with pytest.raises(AgentError) as exc:
        changed.seal(
            2,
            batch["batch_id"],
            SealLabels(
                request_id=uuid4(),
                dataset_hash=batch["frozen"]["dataset"]["dataset_hash"],
                independent_from_judge=True,
            ),
        )
    assert exc.value.code == "judge_protocol_changed"
    assert svc.read(1, batch["batch_id"])["seal"] is None
