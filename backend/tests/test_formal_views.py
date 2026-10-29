import json

from experiments.labels import payload


def test_final_only_view_never_leaks_sql_rule_verdict_gold_or_tool_result(tmp_path):
    data = {
        "before": [],
        "after": [{"booking_id": "actual-sql"}],
        "output": {"assistant_text": "预约成功"},
        "expected": "secret-gold",
        "human_labels": "secret-label",
        "events": [
            {"type": "input.received", "payload": {"text": "我的实际请求"}},
            {"type": "assertion.completed", "payload": {"verdict": "failed"}},
            {
                "type": "tool.completed",
                "event_id": "tool-1",
                "payload": {
                    "tool": "confirm_booking",
                    "raw_result": {"booking_id": "actual-sql"},
                    "visible_result": {"status": "unknown"},
                    "verdict": "secret-rule",
                },
            },
        ],
    }
    (tmp_path / "observations.json").write_text(json.dumps(data), encoding="utf-8")
    limited = payload(tmp_path)
    assert limited["evidence"] == [] and limited["reply"] == "预约成功"
    serialized = json.dumps(limited)
    assert "actual-sql" not in serialized and "secret" not in serialized
    full = payload(tmp_path, full=True)
    assert full["evidence"][1]["content"] == data["after"]
    tool = full["evidence"][2]
    assert tool["id"] == "tool-1" and tool["content"]["visible_result"] == {"status": "unknown"}
    assert "verdict" not in tool["content"] and "secret" not in json.dumps(full)
