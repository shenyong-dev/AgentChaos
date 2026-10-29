"""Synthetic SQL/Redis-free-provider controls; never actual human gold or formal data."""

import os
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_database import TEST_PASSWORD, disposable_database, migrate

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import Settings
from app.db.seeds import seed_database
from app.judging.engine import manifest
from app.schemas.judging import HumanLabel, SealLabels
from experiments.labels import LabelService
from experiments.schemas import ExecutionGoldLabel


@pytest.mark.skipif(os.getenv("AC_INTEGRATION") != "1", reason="Disposable T16 MySQL control")
def test_gold_requires_distinct_execution_label_auth_hash_and_immutable_seal():
    s = Settings()
    with disposable_database("T16") as engine:
        migrate(engine)
        seed_database(engine, TEST_PASSWORD)
        service = LabelService(engine, None, s)
        batch = uuid4()
        observed = {"dialogue": [], "reply": "明确离线机械控制，不是真实金标准", "evidence": []}
        samples = [
            {
                "id": str(i),
                "sample_hash": canonical_hash(observed),
                "payload": observed,
                "provenance": {"kind": "synthetic infrastructure control"},
            }
            for i in range(30)
        ]
        dataset = {"samples": samples, "view": "full_observations"}
        dataset["dataset_hash"] = canonical_hash(dataset)
        service._append(
            1,
            str(batch),
            "judge_request",
            {
                "dataset": dataset,
                "manifest": manifest(s),
                "mode": "offline_double",
            },
            "request",
        )
        base = {
            "support": "supported",
            "explanation": "reasonable",
            "sample_hash": canonical_hash(observed),
            "note": "机械测试，非实际人工标签",
        }
        with pytest.raises(ValidationError):
            ExecutionGoldLabel(request_id=uuid4(), **base)
        with pytest.raises(AgentError) as missing:
            service.label(2, batch, "0", HumanLabel(request_id=uuid4(), **base))
        assert missing.value.code == "execution_gold_required"
        request = ExecutionGoldLabel(request_id=uuid4(), execution_problem="clear", **base)
        with pytest.raises(AgentError) as wrong_role:
            service.label_execution(1, batch, "0", request)
        assert wrong_role.value.status_code == 403
        for sample in samples:
            request = ExecutionGoldLabel(request_id=uuid4(), execution_problem="clear", **base)
            saved = service.label_execution(2, batch, sample["id"], request)
            assert saved["execution_problem"] == "clear"
            assert service.label_execution(2, batch, sample["id"], request) == saved
        sealed = service.seal(
            2,
            batch,
            SealLabels(
                request_id=uuid4(),
                dataset_hash=dataset["dataset_hash"],
                independent_from_judge=True,
            ),
        )
        assert len(sealed["labels"]) == 30
        with pytest.raises(AgentError) as frozen:
            service.label_execution(
                2,
                batch,
                "0",
                ExecutionGoldLabel(request_id=uuid4(), execution_problem="problem", **base),
            )
        assert frozen.value.code == "judge_labels_sealed"
        original = service.read(2, batch)
        service._append(
            1,
            batch,
            "judge_review",
            {
                "source": "assistant_analysis_requested_by_user",
                "original_labels_hash": sealed["labels_hash"],
                "samples": {
                    "0": {
                        "recommendation": {"execution_problem": "problem"},
                        "reason": "明确机械控制，非正式助手判断",
                    }
                },
            },
            "assistant_analysis",
        )
        after = service.read(2, batch)
        assert after["seal"] == original["seal"] and after["labels"] == original["labels"]
        assert after["results"] == after["reviews"] == {}
        assert after["assistant_reviews"]["0"]["recommendation"]["execution_problem"] == "problem"
