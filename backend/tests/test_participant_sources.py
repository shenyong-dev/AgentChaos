"""T17A full-input provenance, including hostile model-selected source substrings."""

from copy import deepcopy

import pytest

from app.agents.errors import AgentError
from app.agents.participants import fields_for_input
from app.agents.state import FieldPatch, patch_fields


def current(text, fields=None):
    return {
        "last_user_text": text,
        "fields": fields or {},
        "turn_index": 2,
        "reference_clock": "2026-10-30T09:00:00+08:00",
    }


@pytest.mark.parametrize(
    "text,quote,value",
    [
        ("-2人", "2人", 2),
        ("−2人", "2人", 2),
        ("负二人", "二人", 2),
        ("12人", "2人", 2),
        ("十二人", "二人", 2),
        ("2.5人", "5人", 5),
        ("2.0人", "2", 2),
        (".5人", "5人", 5),
        ("8到12人", "12人", 12),
        ("8-12人", "12人", 12),
        ("8人至12人", "8人", 8),
        ("8人或12人", "12人", 12),
        ("不是8人，改为12人", "8人", 8),
        ("不要8人", "8人", 8),
        ("至少8人", "8人", 8),
        ("8人左右", "8人", 8),
        ("8个人以上", "8个人", 8),
        ("人数未定，之前8人", "8人", 8),
        ("十十人", "十人", 10),
        ("二三人", "三人", 3),
        ("A101人", "101人", 101),
        ("人数改为2亿人", "2", 2),
        ("20多人", "20", 20),
        ("1e2人", "2人", 2),
        ("人数改为0人", "0人", 1),
        ("＋8人", "8人", 8),
    ],
)
def test_uncertain_invalid_negated_or_partial_number_cannot_become_fact(text, quote, value):
    with pytest.raises(AgentError) as caught:
        patch_fields(current(text), FieldPatch(attendees=value, source_quotes={"attendees": quote}))
    assert caught.value.code == "field_ambiguous"


@pytest.mark.parametrize(
    "text,quote,value",
    [
        ("明天A101，14:00到15:00，12人", "12人", 12),
        ("十二人", "十二人", 12),
        ("两个人", "两个人", 2),
        ("二十人", "二十人", 20),
        ("三十五名参会者", "三十五名参会者", 35),
        ("10位参会者", "10位参会者", 10),
        ("８人", "８人", 8),
        ("8", "8", 8),
        ("人数改为12", "12", 12),
        ("不是8人，改为12人", "12人", 12),
        ("8人改为12人", "12人", 12),
        ("8人，不是12人", "8人", 8),
        ("原计划8人，现在12人", "12人", 12),
        ("一百零二人", "一百零二人", 102),
        ("10人，时长60分钟", "10人", 10),
    ],
)
def test_explicit_whole_counts_and_corrections_remain_usable(text, quote, value):
    fields, changed = patch_fields(
        current(text), FieldPatch(attendees=value, source_quotes={"attendees": quote})
    )
    assert changed and fields["attendees"]["value"] == value
    assert fields["attendees"]["source_quote"] == quote
    assert fields["attendees"]["source_turn"] == 2


@pytest.mark.parametrize("text", ["人数改为-2人", "人数未定", "12人", "不是8人", "8到12人"])
def test_new_quantity_removes_old_count_even_if_model_ignores_update(text):
    state = current("", {"attendees": {"value": 8}, "room_code": {"value": "A101"}})
    original = deepcopy(state)
    fields = fields_for_input(state, text)
    assert "attendees" not in fields and fields["room_code"] == {"value": "A101"}
    assert state == original


def test_unrelated_numbers_do_not_erase_people_and_rejected_patch_is_atomic():
    fields = {"attendees": {"value": 8}, "room_code": {"value": "A101"}}
    assert fields_for_input(current("", fields), "明天A101，14:00到15:00，时长60分钟") == fields
    state = current("明天，-2人", fields)
    original = deepcopy(state)
    with pytest.raises(AgentError):
        patch_fields(
            state,
            FieldPatch(
                date="2026-10-31",
                attendees=2,
                source_quotes={"date": "明天", "attendees": "2人"},
            ),
        )
    assert state == original


@pytest.mark.parametrize(
    "text,quote,value",
    [
        ("2人半", "2人", 2),
        ("第12人", "12人", 12),
        ("不满12人", "12人", 12),
        ("8和12人", "12人", 12),
        ("人数从8改为-2", "2", 2),
        ("人数改为(-2)人", "2", 2),
        ("大约有12人", "12人", 12),
    ],
)
def test_additional_ordinal_fraction_approximate_and_parenthesized_probes(text, quote, value):
    with pytest.raises(AgentError):
        patch_fields(current(text), FieldPatch(attendees=value, source_quotes={"attendees": quote}))
    assert "attendees" not in fields_for_input(current("", {"attendees": {"value": 8}}), text)


def test_label_only_correction_and_undetermined_count_have_safe_boundaries():
    fields, _ = patch_fields(
        current("人数从8改为12"),
        FieldPatch(
            attendees=12,
            source_quotes={"attendees": "12"},
        ),
    )
    assert fields["attendees"]["value"] == 12
    assert fields_for_input(current("", fields), "人数待确定") == {}
