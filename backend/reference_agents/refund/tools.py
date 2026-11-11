from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Order(Strict):
    order_id: str = Field(pattern=r"^O-[0-9]{4}$")


class Preview(Order):
    pass


class Confirm(Strict):
    draft_id: str = Field(min_length=36, max_length=36)
    draft_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=36, max_length=36)


class Lookup(Strict):
    idempotency_key: str = Field(min_length=36, max_length=36)


TOOLS = {
    "query_order": (Order, "查询当前用户订单。不能改身份。"),
    "check_refund": (Order, "校验是否可退，金额由订单决定，单位分。"),
    "preview_refund": (Preview, "生成完整模拟退款草稿；必须展示并等待用户明确确认。"),
    "confirm_refund": (Confirm, "按当前已确认草稿模拟入账；未确认时禁止调用。"),
    "lookup_refund_by_request": (Lookup, "按原请求键查询入账事实；未知回执不能换键重提。"),
}


def schemas():
    return [
        {
            "type": "function",
            "function": {"name": n, "description": d, "parameters": s.model_json_schema()},
        }
        for n, (s, d) in TOOLS.items()
    ]
