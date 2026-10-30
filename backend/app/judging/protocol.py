"""One versioned JSON schema and evidence boundary for all new judge requests."""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.schemas.judging import JudgmentV2

VERSION = "T17B-v2"


def parse_response(content):
    """Reject ambiguous duplicate keys and non-JSON numeric constants, without repairs."""

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value

    def nonfinite(_):
        raise ValueError("Non-finite JSON number")

    return json.loads(content, object_pairs_hook=unique, parse_constant=nonfinite)


def messages(protocol, payload):
    # Allowlist, do not forward sample metadata, human labels, or prior opinions.
    observed = {k: payload[k] for k in ("dialogue", "reply", "dialogue_scope", "evidence")}
    request = {
        "view": "observations" if observed["evidence"] else "answer_only",
        "observed": observed,
    }
    schema = json.dumps(JudgmentV2.model_json_schema(), ensure_ascii=False)
    return [
        SystemMessage(content=protocol["prompt"] + "\nJSON Schema（实际校验模型）：\n" + schema),
        HumanMessage(content=json.dumps(request, ensure_ascii=False)),
    ]
