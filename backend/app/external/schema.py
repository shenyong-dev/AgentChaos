"""Pinned AgentChaos HTTP dialogue protocol v1, deliberately not an A2A implementation."""

import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

PROTOCOL = "agentchaos-http-1"
PREFIX = "external_http:"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Card(Strict):
    protocol: Literal["agentchaos-http-1"]
    name: str = Field(min_length=1, max_length=100)
    revision: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    # First version is a dialogue port: no business/tool/retrieval assertions are invented.
    side_effects: Literal[False]
    request_lookup: Literal[True]
    cancellation: Literal[True]
    cancel_before_execute: Literal[True]
    billing: Literal["free", "external_account"]

    @field_validator(
        "side_effects", "request_lookup", "cancellation", "cancel_before_execute", mode="before"
    )
    @classmethod
    def actual_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("Actual JSON boolean required")
        return value


class Entry(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    endpoint: str = Field(min_length=1, max_length=500)
    pinned_ip: str = Field(min_length=1, max_length=64)
    network_zone: Literal["public", "approved_private"] = "public"
    credential_env: str = Field(pattern=r"^AGENTCHAOS_EXTERNAL_[A-Z0-9_]{1,64}$")
    card: Card
    enabled: bool = False
    timeout_seconds: int = Field(default=20, ge=1, le=45)
    cost_upper_microusd: int = Field(default=0, ge=0, le=100_000)
    token_upper: int = Field(default=8192, ge=1, le=131072)
    author_relation: Literal["project_example", "external_developer"]

    @model_validator(mode="after")
    def billing_consistent(self):
        if (self.card.billing == "free") != (self.cost_upper_microusd == 0):
            raise ValueError("Explicit billing and request upper allowance required")
        return self


PathField = Annotated[
    str, Field(max_length=160, pattern=r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){0,9}$")
]
InputField = Annotated[str, Field(max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")]
ServicePath = Annotated[
    str, Field(max_length=240, pattern=r"^/[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*/?$")
]
HttpProtocol = Literal["http-json", "dify"]
ConnectProtocol = Literal["agentchaos-http-1", "http-json", "dify"]


class HttpProfile(Strict):
    message_field: InputField | None = "message"
    answer_path: PathField = "answer"
    session_field: InputField | None = None
    session_path: PathField | None = None
    history_field: InputField | None = None
    retrieval_path: PathField | None = None
    document_id_path: PathField = "document_id"
    readback_path: ServicePath | None = None
    readback_id_path: PathField = "request_id"
    sandbox_tool_fault: bool = False
    body_fields: dict[str, JsonValue] = Field(default_factory=dict, max_length=12)

    @model_validator(mode="after")
    def mappings(self):
        if not (self.message_field or self.history_field):
            raise ValueError("The request needs a message or history field")
        keys = [k for k in (self.message_field, self.session_field, self.history_field) if k]
        if len(keys) != len(set(keys)) or set(keys).intersection(self.body_fields):
            raise ValueError("Each input field must have a single source")
        if bool(self.session_field) != bool(self.session_path):
            raise ValueError("The request and response session mappings must both be set")
        if self.session_field and self.history_field:
            raise ValueError("Choose a remote session or a message history")
        if len(json.dumps(self.body_fields, ensure_ascii=False, allow_nan=False).encode()) > 4096:
            raise ValueError("Additional JSON body fields exceed 4 KB")
        return self


class HttpCard(Strict):
    protocol: HttpProtocol
    name: str = Field(min_length=1, max_length=100)
    revision: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    billing: Literal["free", "external_account"]


class HttpEntry(Entry):
    card: HttpCard
    http: HttpProfile

    @model_validator(mode="after")
    def separate_readback(self):
        from urllib.parse import urlsplit

        if self.http.readback_path and (
            self.http.readback_path.rstrip("/") == urlsplit(self.endpoint).path.rstrip("/")
        ):
            raise ValueError("The read-only endpoint must have a separate path")
        return self


class Registry(Strict):
    schema_version: Literal["agentchaos-external-registry-1"]
    targets: list[Entry | HttpEntry] = Field(max_length=30)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({entry.id for entry in self.targets}) != len(self.targets):
            raise ValueError("Duplicate registered target")
        return self


class Binding(Strict):
    tenant: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: str = Field(pattern=r"^test:[A-Za-z0-9][A-Za-z0-9:_-]{0,58}$")
    session_id: str = Field(pattern=r"^[a-f0-9-]{36}$")


class Envelope(Strict):
    protocol: Literal["agentchaos-http-1"]
    revision: str
    operation: Literal["capabilities", "prepare", "execute", "lookup", "cancel"]
    request_id: str
    binding: Binding
    data: dict[str, JsonValue]

    @model_validator(mode="after")
    def uuid_request(self):
        UUID(self.request_id)
        UUID(self.binding.session_id)
        return self


class Limits(Strict):
    max_model_calls: int = Field(ge=1, le=1)
    max_tokens: int = Field(ge=1, le=131072)
    max_cost_microusd: int = Field(ge=0, le=100_000)
    timeout_seconds: int = Field(ge=1, le=45)


class Send(Strict):
    text: str = Field(min_length=1, max_length=12000)
    expected_revision: int = Field(ge=0)
    limits: Limits


class Usage(Strict):
    model_calls: int = Field(ge=0, le=1)
    tokens: int = Field(ge=0, le=131072)
    cost_microusd: int = Field(ge=0, le=100000)


class Result(Strict):
    state: Literal["completed", "waiting_input", "working", "failed", "cancelled"]
    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    revision: int = Field(ge=0)
    assistant_text: str = Field(max_length=16000)
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    usage: Usage | None = None


class Lookup(Strict):
    status: Literal["found", "not_found", "unknown"]
    original_operation: Literal["prepare", "execute"]
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    result: dict[str, JsonValue] | None = None


class Cancel(Strict):
    status: Literal["requested", "confirmed", "already_terminal", "not_supported"]


def unique_json(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON field")
        value[key] = item
    return value
