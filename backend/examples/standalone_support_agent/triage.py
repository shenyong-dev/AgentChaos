"""A deterministic support-triage agent with session-local state."""

import re

ORDER = re.compile(r"\bSO-\d{6}\b", re.IGNORECASE | re.ASCII)


def reply(previous, message):
    state = previous.copy()
    if order := ORDER.search(message):
        number = order[0].upper()
        if state.get("order") not in (None, number):
            state.pop("issue", None)
        state["order"] = number
    if any(word in message for word in ("破损", "碎了", "裂开")):
        state["issue"] = "damaged"
    elif any(word in message for word in ("没收到", "未收到", "物流停滞")):
        state["issue"] = "delivery"
    if "order" not in state:
        return state, "请提供订单编号，格式如 SO-123456。"
    if "issue" not in state:
        return state, f"已记录订单 {state['order']}。请描述遇到的问题。"
    order = state["order"]
    if state["issue"] == "damaged":
        return state, f"订单 {order} 建议归入商品破损，请补充商品与外包装照片。尚未创建工单。"
    return state, f"订单 {order} 建议归入物流查询，请补充最近一次物流更新时间。尚未创建工单。"
