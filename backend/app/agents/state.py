"""Structured facts with input spans; ambiguity does not become a guessed time."""

import re
from datetime import date, datetime, timedelta
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field

from app.agents.errors import AgentError
from app.schemas.booking import AvailabilityQuery
from app.services.booking import LOCAL_ZONE


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    session_id: str
    actor_key: str
    scope_id: str
    reference_clock: str
    version_id: int
    fields: dict
    turn_index: int
    last_user_text: str
    pending_draft: dict | None
    confirmation_state: str
    confirmation_revision: int
    grant: dict | None
    denials: list[dict]
    business_result: dict | None
    unknown_submission: dict | None
    candidate_rooms: list[dict]
    retrieval_result: dict | None
    evidence_refs: list[str]
    audit: list[dict]
    model_calls_this_turn: int
    resume_action: dict | None
    last_error: dict | None
    compactions: int
    last_tool_result: dict | None
    write_attempted_this_turn: bool
    business_history: list[dict]


class FieldPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="当地日期YYYY-MM-DD；相对日期按服务端参考时钟解析。",
    )
    start_time: str | None = Field(
        default=None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="当地钟表时间HH:MM，例如14:00；不含日期、秒或时区。",
    )
    end_time: str | None = Field(
        default=None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="当地钟表时间HH:MM，例如15:00；不含日期、秒或时区。",
    )
    duration_minutes: int | None = Field(
        default=None,
        strict=True,
        ge=30,
        le=120,
        description="仅当用户明确说时长时填写；已给起止时间就分别填写start_time和end_time。",
    )
    attendees: int | None = Field(default=None, strict=True, ge=1, le=2**31 - 1)
    room_code: str | None = None
    source_quotes: dict[str, str] = Field(
        default_factory=dict,
        description="每个本次填写的字段都要有同名键，对应当前用户输入的原文片段；保留的旧字段不要重复填写。",
    )
    clear_fields: list[str] = Field(default_factory=list, max_length=6)


def number(text):
    if text.isdigit():
        return int(text)
    digits = {char: i for i, char in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "十" in text:
        left, right = text.split("十", 1)
        return (digits.get(left, 1) if left else 1) * 10 + (digits.get(right, 0) if right else 0)
    return digits.get(text)


def dates_from_quote(quote: str, reference: datetime):
    today = reference.astimezone(LOCAL_ZONE).date()
    result = set()
    for word, offset in (("今天", 0), ("明天", 1), ("后天", 2)):
        if word in quote:
            result.add((today + timedelta(days=offset)).isoformat())
    for year, month, day in re.findall(r"(?:(\d{4})[年-])?(\d{1,2})[月-](\d{1,2})日?", quote):
        try:
            result.add(date(int(year) if year else today.year, int(month), int(day)).isoformat())
        except ValueError:
            continue
    return result


def times_from_quote(quote: str):
    result = set()
    daypart = "下午" if re.search("下午|晚上|傍晚", quote) else None
    for part, hour, minute, chinese_minute in re.findall(
        r"(上午|下午|晚上|早上|中午)?([0-9一二三四五六七八九十两]{1,3})(?::(\d{2})|点([0-9一二三四五六七八九十两]{0,3})(?:分)?)",
        quote,
    ):
        h = number(hour)
        m = number(minute or chinese_minute) if minute or chinese_minute else 0
        if h is None or m is None:
            continue
        if (part in ("下午", "晚上", "中午") or (not part and daypart)) and h < 12:
            h += 12
        if 0 <= h < 24 and 0 <= m < 60:
            result.add(f"{h:02d}:{m:02d}")
    return result


def patch_fields(state: AgentState, patch: FieldPatch):
    fields = {key: dict(value) for key, value in state.get("fields", {}).items()}
    current = state["last_user_text"]
    reference = datetime.fromisoformat(state["reference_clock"])
    changed = False
    for key in patch.clear_fields:
        quote = patch.source_quotes.get(key, "")
        if (
            key not in FieldPatch.model_fields
            or not quote
            or quote not in current
            or not re.search("任意|不限|随便|未定|待定|不确定|不指定", quote)
        ):
            raise AgentError("field_source_invalid", "清除字段必须有当前用户输入依据。", 400)
        changed = changed or key in fields
        fields.pop(key, None)
    for key, value in patch.model_dump(exclude={"source_quotes", "clear_fields"}).items():
        if value is None:
            continue
        quote = patch.source_quotes.get(key, "")
        if not quote or len(quote) > 240 or quote not in current:
            raise AgentError(
                "field_source_invalid",
                f"{key}缺少source_quotes.{key}或引用不在当前输入中；仅填写本次有依据的字段。",
                400,
            )
        valid = False
        if key == "date":
            valid = value in dates_from_quote(quote, reference)
        elif key in ("start_time", "end_time"):
            valid = value in times_from_quote(quote)
        elif key == "attendees":
            nums = re.findall(r"[0-9一二三四五六七八九十两]+", quote)
            standalone_number = quote.strip() == current.strip() and re.fullmatch(
                r"[0-9一二三四五六七八九十两]+", quote.strip()
            )
            valid = ("人" in quote or standalone_number) and any(
                number(item) == value for item in nums
            )
        elif key == "duration_minutes":
            durations = set()
            for amount, unit in re.findall(
                r"([0-9一二三四五六七八九十两]+)(个?小时|分钟|分)", quote
            ):
                parsed = number(amount)
                if parsed is not None:
                    durations.add(parsed * (60 if "小时" in unit else 1))
            if "半小时" in quote:
                durations.add(30)
            valid = value in durations
        elif key == "room_code":
            value = value.upper()
            valid = value in [match.upper() for match in re.findall(r"[A-Za-z]\d{3}", quote)]
        if not valid:
            raise AgentError(
                "field_ambiguous",
                f"原文不足以确定{key}。时间字段只填HH:MM；已给起止时间时分别填写，不用duration_minutes。",
                400,
            )
        changed = changed or fields.get(key, {}).get("value") != value
        fields[key] = {
            "value": value,
            "source_turn": state["turn_index"],
            "source_quote": quote,
            "source_kind": "validated_model_extraction",
        }
    # An explicit new duration supersedes the previously supplied end, and vice versa.
    # Keeping both would silently ignore the user's correction in booking_request.
    if patch.duration_minutes is not None and patch.end_time is None:
        changed = changed or "end_time" in fields
        fields.pop("end_time", None)
    elif patch.end_time is not None and patch.duration_minutes is None:
        changed = changed or "duration_minutes" in fields
        fields.pop("duration_minutes", None)
    if all(key in fields for key in ("start_time", "end_time", "duration_minutes")):
        start = datetime.strptime(fields["start_time"]["value"], "%H:%M")
        end = datetime.strptime(fields["end_time"]["value"], "%H:%M")
        if (end - start).total_seconds() != fields["duration_minutes"]["value"] * 60:
            raise AgentError(
                "time_fields_conflict",
                "起止时间与明确时长矛盾，请先澄清；不能选择其中一项提交。",
                400,
            )
    return fields, changed


def booking_request(state: AgentState, *, room_code=None):
    fields = {key: item["value"] for key, item in state.get("fields", {}).items()}
    needed = [key for key in ("date", "start_time", "attendees") if key not in fields]
    if "end_time" not in fields and "duration_minutes" not in fields:
        needed.append("end_time_or_duration")
    if needed:
        raise AgentError("fields_incomplete", "还需澄清：" + ", ".join(needed), 400)
    start = datetime.fromisoformat(fields["date"] + "T" + fields["start_time"]).replace(
        tzinfo=LOCAL_ZONE
    )
    end = (
        datetime.fromisoformat(fields["date"] + "T" + fields["end_time"]).replace(tzinfo=LOCAL_ZONE)
        if "end_time" in fields
        else start + timedelta(minutes=fields["duration_minutes"])
    )
    return AvailabilityQuery(
        start_at=start,
        end_at=end,
        attendees=fields["attendees"],
        room_code=room_code or fields.get("room_code"),
    )
