from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.contracts import TestCaseDraft as CaseDraft


@pytest.fixture
def example():
    return {
        "case_key": "MEM-DATE-001",
        "revision": 1,
        "family_key": "date-change-01",
        "category": "memory_and_tool",
        "dataset_split": "dev",
        "reference_clock": "2026-10-24T09:00:00+08:00",
        "fixture": "rooms-v1-empty",
        "turns": [
            {"user": "查询A101，10月26日14点到15点，20人。"},
            {"user": "改成10月27日，其他不变。"},
            {"user": "确认预约。"},
        ],
        "assertions": [
            {"type": "booking_matches", "date": "2026-10-27", "attendees": 20},
            {"type": "booking_count", "equals": 1},
            {"type": "no_booking_on", "date": "2026-10-26"},
        ],
        "review_status": "draft",
    }


def test_multiturn_example_roundtrips_without_claiming_execution(example):
    parsed = CaseDraft.model_validate(example)
    restored = CaseDraft.model_validate_json(parsed.model_dump_json())
    assert restored == parsed
    assert restored.review_status == "draft"
    assert restored.reference_clock.utcoffset().total_seconds() == 8 * 3600


def test_reference_clock_requires_explicit_timezone(example):
    example["reference_clock"] = "2026-10-24T09:00:00"
    with pytest.raises(ValidationError):
        CaseDraft.model_validate(example)


def test_contract_rejects_unrecognized_assertions_and_identity_injection(example):
    unknown_assertion = deepcopy(example)
    unknown_assertion["assertions"] = [{"type": "invented_check", "expected": True}]
    with pytest.raises(ValidationError):
        CaseDraft.model_validate(unknown_assertion)
    example["turns"][0]["actor_key"] = "another-user"
    with pytest.raises(ValidationError):
        CaseDraft.model_validate(example)
