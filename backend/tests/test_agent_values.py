from uuid import uuid4

import pytest
from agent_test_support import REFERENCE
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.context import compose_context
from app.agents.errors import AgentError
from app.agents.state import FieldPatch, booking_request, patch_fields


def state(text, fields=None, turn=1):
    return {
        "reference_clock": REFERENCE.isoformat(),
        "last_user_text": text,
        "turn_index": turn,
        "fields": fields or {},
        "messages": [],
        "denials": [],
    }


def test_ambiguous_afternoon_and_room_number_do_not_become_time_or_attendees():
    for text, patch in (
        ("明天下午预约", FieldPatch(start_time="14:00", source_quotes={"start_time": "下午"})),
        ("A101", FieldPatch(attendees=101, source_quotes={"attendees": "A101"})),
    ):
        with pytest.raises(AgentError, match="原文不足"):
            patch_fields(state(text), patch)


def test_partial_change_preserves_other_field_sources_and_chinese_duration():
    text = "明天下午两点，预约一个小时，十人，A101。"
    fields, changed = patch_fields(
        state(text),
        FieldPatch(
            date="2026-10-26",
            start_time="14:00",
            duration_minutes=60,
            attendees=10,
            room_code="A101",
            source_quotes={
                "date": "明天",
                "start_time": "下午两点",
                "duration_minutes": "一个小时",
                "attendees": "十人",
                "room_code": "A101",
            },
        ),
    )
    assert changed
    request = booking_request(state(text, fields))
    assert request.end_at.hour == 15 and request.attendees == 10
    updated, changed = patch_fields(
        state("人数改为30人", fields, 2),
        FieldPatch(attendees=30, source_quotes={"attendees": "30人"}),
    )
    assert changed and updated["attendees"]["source_turn"] == 2
    assert updated["date"]["source_turn"] == 1 and updated["room_code"]["value"] == "A101"


def test_source_quotes_must_be_from_current_input_and_fields_must_be_complete():
    with pytest.raises(AgentError) as caught:
        patch_fields(
            state("请问规则"), FieldPatch(attendees=10, source_quotes={"attendees": "10人"})
        )
    assert caught.value.code == "field_source_invalid"
    with pytest.raises(AgentError, match="还需澄清"):
        booking_request(state("明天"))


def test_duration_correction_replaces_old_end_time_without_changing_other_sources():
    original = {
        "date": {"value": "2026-10-26", "source_turn": 1},
        "start_time": {"value": "14:00", "source_turn": 1},
        "end_time": {"value": "15:00", "source_turn": 1},
        "attendees": {"value": 10, "source_turn": 1},
    }
    fields, changed = patch_fields(
        state("时长改成2小时", original, 2),
        FieldPatch(duration_minutes=120, source_quotes={"duration_minutes": "2小时"}),
    )
    assert changed and "end_time" not in fields
    assert booking_request(state("", fields)).end_at.hour == 16
    assert (
        fields["start_time"]["source_turn"] == 1 and fields["duration_minutes"]["source_turn"] == 2
    )


def test_end_correction_replaces_old_duration():
    fields, changed = patch_fields(
        state("结束改成15:30", {"duration_minutes": {"value": 60, "source_turn": 1}}, 2),
        FieldPatch(end_time="15:30", source_quotes={"end_time": "15:30"}),
    )
    assert changed and "duration_minutes" not in fields and fields["end_time"]["value"] == "15:30"


def test_explicit_inconsistent_time_and_duration_need_clarification():
    with pytest.raises(AgentError) as caught:
        patch_fields(
            state("14:00到15:00，时长2小时"),
            FieldPatch(
                start_time="14:00",
                end_time="15:00",
                duration_minutes=120,
                source_quotes={
                    "start_time": "14:00",
                    "end_time": "15:00",
                    "duration_minutes": "2小时",
                },
            ),
        )
    assert caught.value.code == "time_fields_conflict"


def test_compaction_keeps_tool_pairs_and_preserves_corrections_and_refusals():
    messages = []
    for i in range(12):
        call_id = str(uuid4())
        messages.extend(
            [
                HumanMessage(content=f"轮次{i}：" + "讨论背景。" * 180, id=str(uuid4())),
                AIMessage(
                    content="",
                    id=str(uuid4()),
                    tool_calls=[
                        {"id": call_id, "name": "retrieve_rules", "args": {"query": "人数"}}
                    ],
                ),
                ToolMessage(
                    content='{"status":"supported"}', tool_call_id=call_id, id=str(uuid4())
                ),
                AIMessage(content="已说明规则。", id=str(uuid4())),
            ]
        )
    original = len(messages)
    value = state("最后一轮", {"attendees": {"value": 30, "source_turn": 2}})
    value.update(
        {
            "messages": messages,
            "denials": [{"source_turn": 3, "text": "拒绝确认"}],
            "confirmation_state": "rejected",
        }
    )
    selected, summary = compose_context(value, "测试系统提示", token_budget=7000)
    assert summary["dropped_from_model_context"] > 0 and len(messages) == original
    assert summary["preserved_facts"]["fields"]["attendees"]["value"] == 30
    assert summary["preserved_facts"]["denials"][0]["source_turn"] == 3
    call_ids = {
        call["id"] for item in selected if isinstance(item, AIMessage) for call in item.tool_calls
    }
    result_ids = {item.tool_call_id for item in selected if isinstance(item, ToolMessage)}
    assert call_ids == result_ids
