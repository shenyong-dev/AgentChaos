"""The twelve platform tables; no task execution or model calls in T02."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Row, SnapshotJSON, UTCDateTime, key_string, table_args


class User(Row, Base):
    __tablename__ = "ac_users"
    __table_args__ = table_args(
        UniqueConstraint("username"),
        CheckConstraint("role IN ('operator','reviewer')", name="ck_users_role"),
    )
    username: Mapped[str] = mapped_column(key_string())
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))


class Target(Row, Base):
    __tablename__ = "ac_targets"
    __table_args__ = table_args()
    name: Mapped[str] = mapped_column(String(128))
    adapter_type: Mapped[str] = mapped_column(key_string())
    description: Mapped[str] = mapped_column(Text, default="")
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON, default=dict)
    owner_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("ac_users.id"))
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TargetVersion(Row, Base):
    __tablename__ = "ac_target_versions"
    __table_args__ = table_args(
        UniqueConstraint("target_id", "version_no", name="uq_target_version"),
        UniqueConstraint("target_id", "id", name="uq_target_version_owner"),
        CheckConstraint("version_no >= 1", name="ck_target_version_positive"),
    )
    target_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("ac_targets.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    model_config_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    prompt_hash: Mapped[str] = mapped_column(key_string(64))
    graph_revision: Mapped[str] = mapped_column(key_string(128))
    kb_manifest_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    tool_revision: Mapped[str] = mapped_column(key_string(128))
    adapter_revision: Mapped[str] = mapped_column(key_string(128))
    config_hash: Mapped[str] = mapped_column(key_string(64))


class KnowledgeDocument(Row, Base):
    __tablename__ = "ac_knowledge_documents"
    __table_args__ = table_args(
        UniqueConstraint("document_key", "revision", name="uq_knowledge_revision"),
        CheckConstraint("revision >= 1", name="ck_knowledge_revision"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_knowledge_validity",
        ),
    )
    document_key: Mapped[str] = mapped_column(key_string(128))
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(key_string(32))
    source_ref: Mapped[str] = mapped_column(String(512))
    valid_from: Mapped[datetime | None] = mapped_column(UTCDateTime())
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime())
    applicability_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    content: Mapped[str] = mapped_column(LONGTEXT)
    content_hash: Mapped[str] = mapped_column(key_string(64))


class TestCase(Row, Base):
    __tablename__ = "ac_test_cases"
    __test__ = False
    __table_args__ = table_args(
        UniqueConstraint("case_key", "revision", name="uq_case_revision"),
        CheckConstraint("revision >= 1", name="ck_case_revision"),
        CheckConstraint("dataset_split IN ('dev','calibration','holdout')", name="ck_case_split"),
        CheckConstraint("review_status IN ('draft','approved','rejected')", name="ck_case_review"),
        CheckConstraint(
            "category IN ('normal','tool_execution','memory_and_tool',"
            "'retrieval','security','robustness')",
            name="ck_case_category",
        ),
        CheckConstraint(
            "review_status <> 'approved' OR (reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_case_approval_audit",
        ),
        Index("ix_case_filter", "dataset_split", "review_status", "category"),
        Index("ix_case_family", "family_key"),
    )
    case_key: Mapped[str] = mapped_column(key_string(80))
    revision: Mapped[int] = mapped_column(Integer)
    family_key: Mapped[str] = mapped_column(key_string(100))
    category: Mapped[str] = mapped_column(key_string(32))
    dataset_split: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(255))
    turns_json: Mapped[list[dict[str, Any]]] = mapped_column(SnapshotJSON)
    fixture_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    expected_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    review_status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    reviewer_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("ac_users.id")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    content_hash: Mapped[str] = mapped_column(key_string(64))


class FaultProfile(Row, Base):
    __tablename__ = "ac_fault_profiles"
    __table_args__ = table_args(
        UniqueConstraint("profile_key", "revision", name="uq_fault_revision"),
        CheckConstraint("revision >= 1", name="ck_fault_revision"),
        CheckConstraint("fault_type IN ('F01','F02','F03','F04')", name="ck_fault_type"),
        CheckConstraint("max_triggers >= 1", name="ck_fault_triggers"),
    )
    profile_key: Mapped[str] = mapped_column(key_string(80))
    revision: Mapped[int] = mapped_column(Integer)
    fault_type: Mapped[str] = mapped_column(String(3))
    trigger_stage: Mapped[str] = mapped_column(key_string(32))
    target_tool: Mapped[str] = mapped_column(key_string(128))
    parameters_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    seed: Mapped[int] = mapped_column(Integer)
    max_triggers: Mapped[int] = mapped_column(Integer)
    enabled_for_test: Mapped[bool] = mapped_column(Boolean, default=True)


class TestRun(Row, Base):
    __tablename__ = "ac_test_runs"
    __test__ = False
    __table_args__ = table_args(
        UniqueConstraint("run_uuid"),
        CheckConstraint(
            "status IN ('queued','running','cancelling','completed','failed','cancelled')",
            name="ck_run_status",
        ),
        Index("ix_run_status_created", "status", "created_at"),
        Index("ix_run_creator_created", "created_by", "created_at"),
    )
    run_uuid: Mapped[str] = mapped_column(key_string(36))
    target_version_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("ac_target_versions.id")
    )
    created_by: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("ac_users.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    manifest_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    manifest_hash: Mapped[str] = mapped_column(key_string(64))
    budget_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(SnapshotJSON)


class TestExecution(Row, Base):
    __tablename__ = "ac_test_executions"
    __test__ = False
    __table_args__ = table_args(
        UniqueConstraint("execution_uuid"),
        UniqueConstraint("run_id", "case_revision_id", "repeat_no", name="uq_execution_plan"),
        UniqueConstraint("scope_id", name="uq_execution_scope"),
        UniqueConstraint("session_id", name="uq_execution_session"),
        CheckConstraint("repeat_no >= 1 AND attempt_no >= 1", name="ck_execution_numbers"),
        CheckConstraint(
            "status IN ('queued','running','waiting_input','passed','failed','inconclusive',"
            "'error','cancelled','skipped')",
            name="ck_execution_status",
        ),
        CheckConstraint(
            "task_outcome IS NULL OR task_outcome IN "
            "('fulfilled','safely_degraded','unknown','not_fulfilled')",
            name="ck_execution_outcome",
        ),
        Index("ix_execution_run_status", "run_id", "status"),
        Index("ix_execution_lease", "status", "lease_until"),
    )
    execution_uuid: Mapped[str] = mapped_column(key_string(36))
    run_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("ac_test_runs.id"))
    case_revision_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("ac_test_cases.id")
    )
    repeat_no: Mapped[int] = mapped_column(Integer)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    attempt_history_json: Mapped[list[dict[str, Any]]] = mapped_column(SnapshotJSON, default=list)
    seed: Mapped[int] = mapped_column(Integer)
    scope_id: Mapped[str] = mapped_column(key_string())
    session_id: Mapped[str] = mapped_column(key_string(128))
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    task_outcome: Mapped[str | None] = mapped_column(String(24))
    lease_token: Mapped[str | None] = mapped_column(key_string())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    before_state_json: Mapped[dict[str, Any] | None] = mapped_column(SnapshotJSON)
    after_state_json: Mapped[dict[str, Any] | None] = mapped_column(SnapshotJSON)
    final_output_json: Mapped[dict[str, Any] | None] = mapped_column(SnapshotJSON)
    timing_json: Mapped[dict[str, Any] | None] = mapped_column(SnapshotJSON)
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(SnapshotJSON)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(SnapshotJSON)


class TraceEvent(Row, Base):
    __tablename__ = "ac_trace_events"
    __table_args__ = table_args(
        UniqueConstraint("event_uuid"),
        UniqueConstraint("execution_id", "attempt_no", "seq_no", name="uq_event_sequence"),
        UniqueConstraint("execution_id", "attempt_no", "event_uuid", name="uq_event_parent_scope"),
        ForeignKeyConstraint(
            ["execution_id", "attempt_no", "parent_event_uuid"],
            [
                "ac_trace_events.execution_id",
                "ac_trace_events.attempt_no",
                "ac_trace_events.event_uuid",
            ],
            name="fk_event_parent_scope",
        ),
        CheckConstraint("attempt_no >= 1 AND seq_no >= 1", name="ck_event_sequence"),
        CheckConstraint("node_visit_no IS NULL OR node_visit_no >= 1", name="ck_event_visit"),
        Index("ix_event_cursor", "execution_id", "id"),
    )
    event_uuid: Mapped[str] = mapped_column(key_string(36))
    execution_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("ac_test_executions.id")
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    seq_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(key_string(64))
    node_name: Mapped[str | None] = mapped_column(key_string(128))
    node_visit_no: Mapped[int | None] = mapped_column(Integer)
    tool_call_id: Mapped[str | None] = mapped_column(key_string(128))
    parent_event_uuid: Mapped[str | None] = mapped_column(key_string(36))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
    payload_hash: Mapped[str] = mapped_column(key_string(64))


class AssertionResult(Row, Base):
    __tablename__ = "ac_assertion_results"
    __table_args__ = table_args(
        UniqueConstraint(
            "execution_id",
            "assertion_key",
            "evaluator_kind",
            "evaluator_version",
            name="uq_assertion_evaluator",
        ),
        CheckConstraint("evaluator_kind IN ('rule','ai','human')", name="ck_assertion_kind"),
        CheckConstraint(
            "verdict IN ('passed','failed','inconclusive','error','na')",
            name="ck_assertion_verdict",
        ),
    )
    execution_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("ac_test_executions.id")
    )
    assertion_key: Mapped[str] = mapped_column(key_string(100))
    metric_name: Mapped[str] = mapped_column(key_string(100))
    evaluator_kind: Mapped[str] = mapped_column(String(16))
    evaluator_version: Mapped[str] = mapped_column(key_string())
    verdict: Mapped[str] = mapped_column(String(16))
    expected_json: Mapped[Any] = mapped_column(SnapshotJSON)
    actual_json: Mapped[Any | None] = mapped_column(SnapshotJSON)
    evidence_refs_json: Mapped[list[str]] = mapped_column(SnapshotJSON)
    reason: Mapped[str] = mapped_column(Text)
    judge_usage_json: Mapped[dict[str, Any] | None] = mapped_column(SnapshotJSON)


class Defect(Row, Base):
    __tablename__ = "ac_defects"
    __table_args__ = table_args(
        UniqueConstraint("defect_key"),
        ForeignKeyConstraint(
            ["target_id", "fixed_version_id"],
            ["ac_target_versions.target_id", "ac_target_versions.id"],
            name="fk_defect_fixed_target",
        ),
        CheckConstraint(
            "status IN ('open','confirmed','fixed','verified','rejected')", name="ck_defect_status"
        ),
        Index("ix_defect_fingerprint", "target_id", "fingerprint"),
    )
    defect_key: Mapped[str] = mapped_column(key_string(80))
    target_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("ac_targets.id"))
    first_execution_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("ac_test_executions.id")
    )
    category: Mapped[str] = mapped_column(key_string())
    severity: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(key_string())
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    evidence_refs_json: Mapped[list[str]] = mapped_column(SnapshotJSON)
    suspected_cause: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), ForeignKey("ac_users.id")
    )
    review_note: Mapped[str | None] = mapped_column(Text)
    fixed_version_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))


class Artifact(Row, Base):
    __tablename__ = "ac_artifacts"
    __table_args__ = table_args(CheckConstraint("size_bytes >= 0", name="ck_artifact_size"))
    run_id: Mapped[int] = mapped_column(BIGINT(unsigned=True), ForeignKey("ac_test_runs.id"))
    kind: Mapped[str] = mapped_column(key_string())
    relative_path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(key_string())
    size_bytes: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    generated_by_revision: Mapped[str] = mapped_column(key_string(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(SnapshotJSON)
