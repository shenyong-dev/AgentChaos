"""Bounded participant grammar over full user text, never a model-selected substring.

This is a conservative source validator, not a general language understanding model.
Uncertain quantities require clarification; a positive integer inside them is not proof.
"""

import re
from dataclasses import dataclass

POLICY = "participants-source-v1"
DIGITS = "0-9０-９零〇一二三四五六七八九十百千两兩"
TOKEN = re.compile(rf"[+＋\-−﹣－负負]?\s*[{DIGITS}]+(?:[.．点][{DIGITS}]+)?")
UNIT = re.compile(
    r"\s*[)）]?\s*(?P<qualifier>多|余|来|几|万|亿)?(?:个人|人|位(?:参会者|与会者|参与者|同学|学生|老师)?"
    r"|名(?:参会者|与会者|参与者|同学|学生|老师)?)"
)
LABEL = re.compile(
    r"(?:人数|参会人数|参与人数)(?:\s*(?:从|由)[^，,。；;\n]{1,24})?"
    r"\s*(?:为|是|改为|改成|调整为|调整成|更正为|[:：=])?\s*$"
)
UNCERTAIN = re.compile(
    r"人数\s*(?:为|是|改为|改成)?\s*"
    r"(?:未定|待定|待确定|待确认|不确定|不清楚|不知道|不限|还没确定|尚未确定|未知)"
)
QUALIFIER = re.compile(
    r"(?:至少|至多|最多|最少|不超过|不少于|不到|不满|超过|大于|小于|少于|多于|第|"
    r"大约有?|约有?|预计|可能|如果|假如|例如|比如)\s*$"
)
NEGATED = re.compile(r"(?:不是|不要|不选|不需要|别用|非|原来|原先|以前|之前|先前|本来|原计划)\s*$")
RANGE = re.compile(r"\s*(?:个人|人|位|名)?\s*(?:到|至|或|或者|和|与|及|、|[-−—–~～/])\s*")
CORRECTION = re.compile(
    r"[\s,，;；]*(?:人数)?(?:现在(?:为|是)?|目前(?:为|是)?|改为|改成|调整为|调整成|实际(?:为|是)?|更正为|而是|是)\s*"
)
TRANSLATE = str.maketrans("０１２３４５６７８９兩〇", "0123456789两零")


def integer(text: str) -> int | None:
    """Accept Arabic integers and unambiguous conventional Chinese integers <=9999."""
    text = text.translate(TRANSLATE)
    if re.fullmatch(r"[0-9]+", text):
        return int(text) if len(text) <= 10 else None
    text = text.replace("两", "二")
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    if text in digits:
        return digits[text]
    total, digit, last_unit = 0, None, 10000
    for char in text:
        if char in digits:
            if digit not in (None, 0):
                return None
            digit = digits[char]
        elif char in "十百千":
            unit = {"十": 10, "百": 100, "千": 1000}[char]
            if unit >= last_unit or (digit is None and not (unit == 10 and total == 0)):
                return None
            total += (digit if digit is not None else 1) * unit
            digit, last_unit = None, unit
        else:
            return None
    total += digit or 0
    if not 0 < total < 10000:
        return None
    # Canonical spelling rejects colloquial ambiguous 一百二 and malformed 十十.
    canonical, pending_zero = "", False
    for unit, suffix in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        amount = total // unit % 10
        if amount:
            if pending_zero:
                canonical += "零"
            canonical += "零一二三四五六七八九"[amount] + suffix
            pending_zero = False
        elif canonical and total % unit:
            pending_zero = True
    if canonical.startswith("一十"):
        canonical = canonical[1:]
    return total if text == canonical else None


@dataclass(frozen=True)
class Mention:
    start: int
    end: int
    number_end: int
    value: int | None
    negated: bool


def mentions(current: str) -> list[Mention]:
    tokens = list(TOKEN.finditer(current))
    result = []
    for index, token in enumerate(tokens):
        start, end = token.span()
        while start < end and current[start].isspace():
            start += 1
        unit = UNIT.match(current, end)
        prefix = re.split(r"[，,。；;！？!?\n]", current[:start])[-1]
        standalone = current.strip() == current[start:end]
        if not (unit or LABEL.search(prefix) or standalone):
            continue
        final = unit.end() if unit else end
        value = integer(current[start:end])
        before = current[start - 1 : start] if start else ""
        after = current[end : end + 1]
        if (
            (before and re.fullmatch(rf"[A-Za-z{DIGITS}.．点]", before))
            or (not unit and after and not re.match(r"[\s,，。.;；!?！？]", after))
            or (unit and unit.group("qualifier"))
            or QUALIFIER.search(prefix)
            or re.match(r"\s*(?:左右|上下|以上|以下|以内|起|多|余|来|半)", current[final:])
        ):
            value = None
        if index and RANGE.fullmatch(current[tokens[index - 1].end() : start]):
            value = None
        if index + 1 < len(tokens) and RANGE.fullmatch(current[final : tokens[index + 1].start()]):
            value = None
        result.append(Mention(start, final, end, value, bool(NEGATED.search(prefix))))
    return result


def resolved(current: str) -> tuple[bool, list[Mention]]:
    found = mentions(current)
    if UNCERTAIN.search(current):
        return True, []
    affirmative = [item for item in found if not item.negated]
    if len(affirmative) > 1:
        # A stated correction supersedes earlier values; an alternative/range does not.
        last = affirmative[-1]
        if CORRECTION.fullmatch(current[affirmative[-2].end : last.start]):
            affirmative = [last]
    values = {item.value for item in affirmative}
    if len(values) != 1 or None in values or any(not 1 <= v <= 2**31 - 1 for v in values):
        return bool(found), []
    return bool(found), affirmative


def supports(current: str, quote: str, value: int) -> bool:
    _, accepted = resolved(current)
    spans = list(re.finditer(re.escape(quote), current)) if quote else []
    return (
        bool(accepted)
        and all(item.value == value for item in accepted)
        and any(
            span.start() <= item.start and item.number_end <= span.end()
            for span in spans
            for item in accepted
        )
    )


def fields_for_input(state: dict, text: str) -> dict:
    """Explicit new quantities revoke old facts even if the model ignores the update."""
    fields = {key: dict(value) for key, value in state.get("fields", {}).items()}
    mentioned, _ = resolved(text)
    if mentioned:
        fields.pop("attendees", None)
    return fields
