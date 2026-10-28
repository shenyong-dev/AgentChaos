"""Versioned typed evidence. No token events and no client-controlled ownership."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactRef(Strict):
    artifact_id: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1)


class Payload(Strict):
    artifact: ArtifactRef | None = None


class LifecyclePayload(Payload):
    kind: Literal[
        "run.created",
        "run.started",
        "run.completed",
        "run.cancel_requested",
        "run.resumed",
        "execution.started",
        "execution.completed",
        "execution.cancelled",
        "execution.error",
        "execution.waiting_input",
    ]
    status: str
    reason: str | None = None
    attempt: int | None = None
    counts: dict[str, int] | None = None
    before: JsonValue = None
    after: JsonValue = None
    usage: JsonValue = None
    output: JsonValue = None


class NodePayload(Payload):
    kind: Literal["node.started", "node.completed", "node.error"]
    status: Literal["started", "completed", "waiting", "error"]
    elapsed_ms: float | None = None
    code: str | None = None


class ModelPayload(Payload):
    kind: Literal["model.started", "model.completed", "model.error"]
    role: Literal["agent", "case_generator", "judge"] = "agent"
    call_id: str | None = None
    requested_model: str
    response_model: str | None = None
    prompt_hash: str
    config_hash: str
    automatic_retries: Literal[0] = 0
    usage: JsonValue = None
    response: JsonValue = None
    raw_record: JsonValue = None
    elapsed_ms: float | None = None
    code: str | None = None
    model_requests: int | None = None
    late_observation: bool = False


class ToolPayload(Payload):
    kind: Literal["tool.requested", "tool.completed", "tool.error"]
    tool: str
    arguments: JsonValue = None
    raw_result: JsonValue = None
    visible_result: JsonValue = None
    elapsed_ms: float | None = None
    code: str | None = None
    commit_state: str | None = None
    before: JsonValue = None
    after: JsonValue = None
    fault_event_id: UUID | None = None
    dispatched: bool | None = None
    receipt_reused: bool = False


class FaultPayload(Payload):
    kind: Literal["fault.scheduled", "fault.injected", "fault.not_applied"]
    fault_type: Literal["F01", "F02", "F03", "F04"]
    stage: Literal["PRE_DISPATCH", "PRE_COMMIT", "POST_RESULT", "POST_COMMIT"]
    profile_id: int
    profile_revision: int
    profile_hash: str
    tool: str
    eligible_call_no: int
    trigger_no: int
    max_triggers: int
    seed: int
    execution_seed: int
    decision_hash: str
    dispatched: bool
    raw_result: JsonValue = None
    visible_result: JsonValue = None
    before: JsonValue = None
    after: JsonValue = None
    rollback_probe: JsonValue = None
    commit_state: str | None = None
    reason: str | None = None


class RetrievalPayload(Payload):
    kind: Literal["retrieval.completed", "retrieval.error"]
    query: str
    result: JsonValue


class StatePayload(Payload):
    kind: Literal["state.changed", "context.compacted", "checkpoint.restored"]
    source_turn: int | None = None
    before: JsonValue = None
    after: JsonValue = None
    summary: JsonValue = None
    checkpoint_id: str | None = None
    next_nodes: list[str] | None = None


class ConfirmationPayload(Payload):
    kind: Literal["confirmation.requested", "confirmation.resumed"]
    draft: JsonValue
    revision: int
    decision: str | None = None
    request_id: str | None = None


class InputPayload(Payload):
    kind: Literal["input.received"]
    request_id: UUID
    source: Literal["script", "input", "recovery"]
    text: str | None = None
    expected_revision: int


class BudgetPayload(Payload):
    kind: Literal["budget.exceeded"]
    code: str
    usage: JsonValue


class AssertionPayload(Payload):
    kind: Literal["assertion.completed"]
    assertion_key: str
    assertion_type: str
    evaluator_version: str
    code_version: str
    verdict: Literal["passed", "failed", "inconclusive", "error", "na"]
    expected: JsonValue
    actual: JsonValue = None
    reason: str
    references: list[str]


class EvaluationPayload(Payload):
    kind: Literal["evaluation.completed"]
    evaluator_version: str
    code_version: str
    input_hash: str
    verdict: Literal["passed", "failed", "inconclusive", "error"]
    task_outcome: Literal["fulfilled", "not_fulfilled", "unknown", "safely_degraded"]
    counts: dict[str, int]


EventPayload = Annotated[
    LifecyclePayload
    | NodePayload
    | ModelPayload
    | ToolPayload
    | RetrievalPayload
    | StatePayload
    | ConfirmationPayload
    | InputPayload
    | BudgetPayload
    | FaultPayload
    | AssertionPayload
    | EvaluationPayload,
    Field(discriminator="kind"),
]


class TraceEvent(Strict):
    schema_version: Literal["1.1", "1.2", "1.3"] = "1.3"
    event_id: UUID
    run_id: UUID
    execution_id: UUID | None
    scope: Literal["run", "execution"]
    attempt: int | None = Field(default=None, ge=1)
    seq: int = Field(ge=1)
    run_seq: int = Field(ge=1)
    type: str
    node: str | None = None
    node_visit: int | None = Field(default=None, ge=1)
    tool_call_id: str | None = None
    parent_event_id: UUID | None = None
    timestamp: AwareDatetime
    payload: EventPayload
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        if self.type != self.payload.kind:
            raise ValueError("Event type/payload mismatch")
        if (self.scope == "run") != (self.execution_id is None):
            raise ValueError("Run events cannot invent an execution identity")
        if (self.execution_id is None) != (self.attempt is None):
            raise ValueError("Execution events require an attempt")
        return self
