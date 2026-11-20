"""Declarative JSON calls and independent read-only checks for existing Agent APIs."""

import re
from urllib.parse import urlsplit

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.evidence.recorder import redact
from app.external.dify import chat_result
from app.external.transport import Client, unknown

MISSING = object()


def at(value, path):
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif (
            isinstance(value, list)
            and re.fullmatch(r"0|[1-9][0-9]{0,4}", part)
            and int(part) < len(value)
        ):
            value = value[int(part)]
        else:
            return MISSING
    return value


def documents(value, id_path):
    if not isinstance(value, list) or len(value) > 60:
        return None
    ids = [item if isinstance(item, str) else at(item, id_path) for item in value]
    if any(not isinstance(item, str) or not 0 < len(item) <= 200 for item in ids):
        return None
    return ids


class HttpPort:
    def __init__(self, entry, settings, owner, *, access_token=None):
        self.entry, self.profile, self.settings = entry, entry.http, settings
        self.owner = owner
        self.client = Client(entry, settings, owner=owner, access_token=access_token)

    def verify(self):
        # Dify's authenticated parameters endpoint reads app configuration without sending a turn.
        if self.entry.card.protocol != "dify":
            return False
        path = urlsplit(self.entry.endpoint).path.removesuffix("/chat-messages") + "/parameters"
        result = self.client.json("GET", path=path, response_limit=262144)
        if not isinstance(result, dict) or "user_input_form" not in result:
            raise AgentError("external_dify_parameters", "Dify 应用参数接口返回格式异常。", 409)
        return True

    def send(self, request, conversation, history, traceparent, *, deadline, tool_fault=False):
        profile = self.profile
        request_id, binding, data = request["request_id"], request["binding"], request["data"]
        body = dict(profile.body_fields)
        if profile.message_field:
            body[profile.message_field] = data["text"]
        if profile.session_field:
            body[profile.session_field] = conversation or ""
        if profile.history_field:
            body[profile.history_field] = [*history, {"role": "user", "content": data["text"]}]
        event_reader = None
        if self.entry.card.protocol == "dify":
            body["user"] = "ac-" + canonical_hash([binding["tenant"], binding["session_id"]])[:36]
            body.setdefault("response_mode", "streaming")
            if body["response_mode"] == "streaming":
                event_reader = chat_result
        headers = {
            "X-Request-ID": request_id,
            "Idempotency-Key": request_id,
            "X-AgentChaos-Session": binding["session_id"],
        }
        if traceparent:
            headers["traceparent"] = traceparent
        if tool_fault:
            headers["X-AgentChaos-Test-Fault"] = "tool_error"
        raw = self.client.json(
            "POST",
            body=body,
            extra_headers=headers,
            deadline=deadline,
            response_limit=262144,
            event_reader=event_reader,
        )
        answer = at(raw, profile.answer_path)
        if not isinstance(answer, str) or len(answer) > 16000:
            raise AgentError(
                "external_response_mapping", "已收到 HTTP 响应，但回复字段缺失或格式不符。", 409
            )
        next_session = at(raw, profile.session_path) if profile.session_path else None
        if profile.session_field and (
            not isinstance(next_session, str)
            or not 0 < len(next_session) <= 200
            or conversation is not None
            and next_session != conversation
        ):
            raise AgentError(
                "external_session_mapping", "返回的会话标识缺失或发生变化，请核对映射。", 409
            )
        ids = (
            documents(at(raw, profile.retrieval_path), profile.document_id_path)
            if profile.retrieval_path
            else None
        )
        return redact(
            {
                "state": "completed",
                "revision": data["expected_revision"] + 1,
                "assistant_text": answer,
                "conversation_id": next_session,
                "retrieved_documents": ids,
            },
            self.settings,
        )

    def readback(self, request_id, session_id, *, deadline=None):
        if not self.profile.readback_path:
            return {"status": "not_applicable", "reason": "该对象尚未配置业务查证接口。"}
        try:
            raw = self.client.json(
                "GET",
                path=self.profile.readback_path,
                query={"request_id": request_id, "session_id": session_id},
                deadline=deadline,
            )
            if not isinstance(raw, dict) or at(raw, self.profile.readback_id_path) != request_id:
                raise unknown()
            # A response for another session must never be attached as fact.
            if "session_id" in raw and raw["session_id"] != session_id:
                raise unknown()
            return {
                "status": "observed",
                "origin": "read_only_endpoint",
                "request_id": request_id,
                "data": redact(raw, self.settings),
            }
        except AgentError:
            return {
                "status": "unavailable",
                "request_id": request_id,
                "reason": "只读接口未返回与原请求标识匹配的证据。",
            }
