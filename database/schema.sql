-- AgentChaos public schema; generated from all Alembic upgrades.
-- EMPTY MySQL 8 database only. Never import into the local/cloud working database.
-- No CREATE DATABASE, USE, account passwords or historical records.
-- Schema revision: 20261107_0007
SET NAMES utf8mb4;
SET SESSION time_zone = '+00:00';

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20261025_0001

CREATE TABLE ac_fault_profiles (
    profile_key VARCHAR(80) COLLATE utf8mb4_bin NOT NULL,
    revision INTEGER NOT NULL,
    fault_type VARCHAR(3) NOT NULL,
    trigger_stage VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    target_tool VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    parameters_json JSON NOT NULL,
    seed INTEGER NOT NULL,
    max_triggers INTEGER NOT NULL,
    enabled_for_test BOOL NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_fault_profiles PRIMARY KEY (id),
    CONSTRAINT ck_fault_type CHECK (fault_type IN ('F01','F02','F03','F04')),
    CONSTRAINT ck_fault_triggers CHECK (max_triggers >= 1),
    CONSTRAINT ck_fault_revision CHECK (revision >= 1),
    CONSTRAINT uq_fault_revision UNIQUE (profile_key, revision)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_knowledge_documents (
    document_key VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    revision INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    source_type VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    source_ref VARCHAR(512) NOT NULL,
    valid_from DATETIME(6),
    valid_to DATETIME(6),
    applicability_json JSON NOT NULL,
    content LONGTEXT NOT NULL,
    content_hash VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_knowledge_documents PRIMARY KEY (id),
    CONSTRAINT ck_knowledge_revision CHECK (revision >= 1),
    CONSTRAINT ck_knowledge_validity CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
    CONSTRAINT uq_knowledge_revision UNIQUE (document_key, revision)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_users (
    username VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    `role` VARCHAR(16) NOT NULL,
    enabled BOOL NOT NULL DEFAULT 1,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_users PRIMARY KEY (id),
    CONSTRAINT ck_users_role CHECK (role IN ('operator','reviewer')),
    CONSTRAINT uq_ac_users_username UNIQUE (username)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE sb_rooms (
    scope_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    room_code VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    capacity INTEGER NOT NULL,
    opening_hours_json JSON NOT NULL,
    enabled BOOL NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_sb_rooms PRIMARY KEY (id),
    CONSTRAINT ck_room_capacity CHECK (capacity > 0),
    CONSTRAINT uq_room_scope_id UNIQUE (scope_id, id),
    CONSTRAINT uq_room_code_scope UNIQUE (scope_id, room_code)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_targets (
    name VARCHAR(128) NOT NULL,
    adapter_type VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    description TEXT NOT NULL,
    capabilities_json JSON NOT NULL,
    owner_id BIGINT UNSIGNED NOT NULL,
    archived_at DATETIME(6),
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_targets PRIMARY KEY (id),
    CONSTRAINT fk_ac_targets_owner_id_ac_users FOREIGN KEY(owner_id) REFERENCES ac_users (id)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_test_cases (
    case_key VARCHAR(80) COLLATE utf8mb4_bin NOT NULL,
    revision INTEGER NOT NULL,
    family_key VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
    category VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    dataset_split VARCHAR(16) NOT NULL,
    source VARCHAR(255) NOT NULL,
    turns_json JSON NOT NULL,
    fixture_json JSON NOT NULL,
    expected_json JSON NOT NULL,
    review_status VARCHAR(16) NOT NULL DEFAULT 'draft',
    reviewer_id BIGINT UNSIGNED,
    reviewed_at DATETIME(6),
    content_hash VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_test_cases PRIMARY KEY (id),
    CONSTRAINT ck_case_split CHECK (dataset_split IN ('dev','calibration','holdout')),
    CONSTRAINT ck_case_approval_audit CHECK (review_status <> 'approved' OR (reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL)),
    CONSTRAINT ck_case_review CHECK (review_status IN ('draft','approved','rejected')),
    CONSTRAINT ck_case_revision CHECK (revision >= 1),
    CONSTRAINT ck_case_category CHECK (category IN ('normal','tool_execution','memory_and_tool','retrieval','security','robustness')),
    CONSTRAINT fk_ac_test_cases_reviewer_id_ac_users FOREIGN KEY(reviewer_id) REFERENCES ac_users (id),
    CONSTRAINT uq_case_revision UNIQUE (case_key, revision)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX ix_case_family ON ac_test_cases (family_key);

CREATE INDEX ix_case_filter ON ac_test_cases (dataset_split, review_status, category);

CREATE TABLE sb_booking_drafts (
    draft_uuid VARCHAR(36) COLLATE utf8mb4_bin NOT NULL,
    scope_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    actor_key VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    room_id BIGINT UNSIGNED NOT NULL,
    start_at DATETIME(6) NOT NULL,
    end_at DATETIME(6) NOT NULL,
    attendees INTEGER NOT NULL,
    payload_hash VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    expires_at DATETIME(6) NOT NULL,
    confirmed_at DATETIME(6),
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_sb_booking_drafts PRIMARY KEY (id),
    CONSTRAINT ck_draft_status CHECK (status IN ('pending','confirmed','expired','superseded','rejected')),
    CONSTRAINT ck_draft_values CHECK (end_at > start_at AND attendees > 0),
    CONSTRAINT fk_draft_room_scope FOREIGN KEY(scope_id, room_id) REFERENCES sb_rooms (scope_id, id),
    CONSTRAINT uq_sb_booking_drafts_draft_uuid UNIQUE (draft_uuid),
    CONSTRAINT uq_draft_scope_actor_room UNIQUE (scope_id, actor_key, room_id, id)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_target_versions (
    target_id BIGINT UNSIGNED NOT NULL,
    version_no INTEGER NOT NULL,
    model_config_json JSON NOT NULL,
    prompt_hash VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    graph_revision VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    kb_manifest_json JSON NOT NULL,
    tool_revision VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    adapter_revision VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    config_hash VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_target_versions PRIMARY KEY (id),
    CONSTRAINT ck_target_version_positive CHECK (version_no >= 1),
    CONSTRAINT fk_ac_target_versions_target_id_ac_targets FOREIGN KEY(target_id) REFERENCES ac_targets (id),
    CONSTRAINT uq_target_version_owner UNIQUE (target_id, id),
    CONSTRAINT uq_target_version UNIQUE (target_id, version_no)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE sb_bookings (
    booking_uuid VARCHAR(36) COLLATE utf8mb4_bin NOT NULL,
    scope_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    actor_key VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    room_id BIGINT UNSIGNED NOT NULL,
    draft_id BIGINT UNSIGNED NOT NULL,
    start_at DATETIME(6) NOT NULL,
    end_at DATETIME(6) NOT NULL,
    attendees INTEGER NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'confirmed',
    idempotency_key VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    payload_hash VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_sb_bookings PRIMARY KEY (id),
    CONSTRAINT ck_booking_status CHECK (status IN ('confirmed','cancelled')),
    CONSTRAINT ck_booking_values CHECK (end_at > start_at AND attendees > 0),
    CONSTRAINT fk_booking_draft_scope_actor FOREIGN KEY(scope_id, actor_key, room_id, draft_id) REFERENCES sb_booking_drafts (scope_id, actor_key, room_id, id),
    CONSTRAINT fk_booking_room_scope FOREIGN KEY(scope_id, room_id) REFERENCES sb_rooms (scope_id, id),
    CONSTRAINT uq_sb_bookings_booking_uuid UNIQUE (booking_uuid),
    CONSTRAINT uq_booking_draft UNIQUE (draft_id),
    CONSTRAINT uq_booking_idempotency UNIQUE (scope_id, actor_key, idempotency_key),
    CONSTRAINT uq_booking_scope_room UNIQUE (scope_id, room_id, id)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_test_runs (
    run_uuid VARCHAR(36) COLLATE utf8mb4_bin NOT NULL,
    target_version_id BIGINT UNSIGNED NOT NULL,
    created_by BIGINT UNSIGNED NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    manifest_json JSON NOT NULL,
    manifest_hash VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    budget_json JSON NOT NULL,
    started_at DATETIME(6),
    finished_at DATETIME(6),
    summary_json JSON,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_test_runs PRIMARY KEY (id),
    CONSTRAINT ck_run_status CHECK (status IN ('queued','running','cancelling','completed','failed','cancelled')),
    CONSTRAINT fk_ac_test_runs_created_by_ac_users FOREIGN KEY(created_by) REFERENCES ac_users (id),
    CONSTRAINT fk_ac_test_runs_target_version_id_ac_target_versions FOREIGN KEY(target_version_id) REFERENCES ac_target_versions (id),
    CONSTRAINT uq_ac_test_runs_run_uuid UNIQUE (run_uuid)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX ix_run_creator_created ON ac_test_runs (created_by, created_at);

CREATE INDEX ix_run_status_created ON ac_test_runs (status, created_at);

CREATE TABLE sb_booking_slots (
    scope_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    room_id BIGINT UNSIGNED NOT NULL,
    slot_start DATETIME(6) NOT NULL,
    booking_id BIGINT UNSIGNED NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_sb_booking_slots PRIMARY KEY (id),
    CONSTRAINT ck_slot_alignment CHECK (MINUTE(slot_start) IN (0,30) AND SECOND(slot_start)=0 AND MICROSECOND(slot_start)=0),
    CONSTRAINT fk_slot_booking_scope_room FOREIGN KEY(scope_id, room_id, booking_id) REFERENCES sb_bookings (scope_id, room_id, id),
    CONSTRAINT fk_slot_room_scope FOREIGN KEY(scope_id, room_id) REFERENCES sb_rooms (scope_id, id),
    CONSTRAINT uq_room_slot_scope UNIQUE (scope_id, room_id, slot_start)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_artifacts (
    run_id BIGINT UNSIGNED NOT NULL,
    kind VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    relative_path VARCHAR(512) NOT NULL,
    sha256 VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    size_bytes BIGINT UNSIGNED NOT NULL,
    generated_by_revision VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    metadata_json JSON NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_artifacts PRIMARY KEY (id),
    CONSTRAINT ck_artifact_size CHECK (size_bytes >= 0),
    CONSTRAINT fk_ac_artifacts_run_id_ac_test_runs FOREIGN KEY(run_id) REFERENCES ac_test_runs (id)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_test_executions (
    execution_uuid VARCHAR(36) COLLATE utf8mb4_bin NOT NULL,
    run_id BIGINT UNSIGNED NOT NULL,
    case_revision_id BIGINT UNSIGNED NOT NULL,
    repeat_no INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT '1',
    attempt_history_json JSON NOT NULL,
    seed INTEGER NOT NULL,
    scope_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    session_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    task_outcome VARCHAR(24),
    lease_token VARCHAR(64) COLLATE utf8mb4_bin,
    lease_until DATETIME(6),
    input_snapshot_json JSON NOT NULL,
    before_state_json JSON,
    after_state_json JSON,
    final_output_json JSON,
    timing_json JSON,
    usage_json JSON,
    error_json JSON,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_test_executions PRIMARY KEY (id),
    CONSTRAINT ck_execution_status CHECK (status IN ('queued','running','waiting_input','passed','failed','inconclusive','error','cancelled','skipped')),
    CONSTRAINT ck_execution_outcome CHECK (task_outcome IS NULL OR task_outcome IN ('fulfilled','safely_degraded','unknown','not_fulfilled')),
    CONSTRAINT ck_execution_numbers CHECK (repeat_no >= 1 AND attempt_no >= 1),
    CONSTRAINT fk_ac_test_executions_case_revision_id_ac_test_cases FOREIGN KEY(case_revision_id) REFERENCES ac_test_cases (id),
    CONSTRAINT fk_ac_test_executions_run_id_ac_test_runs FOREIGN KEY(run_id) REFERENCES ac_test_runs (id),
    CONSTRAINT uq_ac_test_executions_execution_uuid UNIQUE (execution_uuid),
    CONSTRAINT uq_execution_plan UNIQUE (run_id, case_revision_id, repeat_no),
    CONSTRAINT uq_execution_scope UNIQUE (scope_id),
    CONSTRAINT uq_execution_session UNIQUE (session_id)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX ix_execution_lease ON ac_test_executions (status, lease_until);

CREATE INDEX ix_execution_run_status ON ac_test_executions (run_id, status);

CREATE TABLE ac_assertion_results (
    execution_id BIGINT UNSIGNED NOT NULL,
    assertion_key VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
    metric_name VARCHAR(100) COLLATE utf8mb4_bin NOT NULL,
    evaluator_kind VARCHAR(16) NOT NULL,
    evaluator_version VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    verdict VARCHAR(16) NOT NULL,
    expected_json JSON NOT NULL,
    actual_json JSON,
    evidence_refs_json JSON NOT NULL,
    reason TEXT NOT NULL,
    judge_usage_json JSON,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_assertion_results PRIMARY KEY (id),
    CONSTRAINT ck_assertion_kind CHECK (evaluator_kind IN ('rule','ai','human')),
    CONSTRAINT ck_assertion_verdict CHECK (verdict IN ('passed','failed','inconclusive','error','na')),
    CONSTRAINT fk_ac_assertion_results_execution_id_ac_test_executions FOREIGN KEY(execution_id) REFERENCES ac_test_executions (id),
    CONSTRAINT uq_assertion_evaluator UNIQUE (execution_id, assertion_key, evaluator_kind, evaluator_version)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE ac_defects (
    defect_key VARCHAR(80) COLLATE utf8mb4_bin NOT NULL,
    target_id BIGINT UNSIGNED NOT NULL,
    first_execution_id BIGINT UNSIGNED NOT NULL,
    category VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    severity VARCHAR(16) NOT NULL,
    fingerprint VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'open',
    evidence_refs_json JSON NOT NULL,
    suspected_cause TEXT,
    reviewer_id BIGINT UNSIGNED,
    review_note TEXT,
    fixed_version_id BIGINT UNSIGNED,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_defects PRIMARY KEY (id),
    CONSTRAINT ck_defect_status CHECK (status IN ('open','confirmed','fixed','verified','rejected')),
    CONSTRAINT fk_ac_defects_first_execution_id_ac_test_executions FOREIGN KEY(first_execution_id) REFERENCES ac_test_executions (id),
    CONSTRAINT fk_ac_defects_reviewer_id_ac_users FOREIGN KEY(reviewer_id) REFERENCES ac_users (id),
    CONSTRAINT fk_defect_fixed_target FOREIGN KEY(target_id, fixed_version_id) REFERENCES ac_target_versions (target_id, id),
    CONSTRAINT fk_ac_defects_target_id_ac_targets FOREIGN KEY(target_id) REFERENCES ac_targets (id),
    CONSTRAINT uq_ac_defects_defect_key UNIQUE (defect_key)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX ix_defect_fingerprint ON ac_defects (target_id, fingerprint);

CREATE TABLE ac_trace_events (
    event_uuid VARCHAR(36) COLLATE utf8mb4_bin NOT NULL,
    execution_id BIGINT UNSIGNED NOT NULL,
    attempt_no INTEGER NOT NULL,
    seq_no INTEGER NOT NULL,
    event_type VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    node_name VARCHAR(128) COLLATE utf8mb4_bin,
    node_visit_no INTEGER,
    tool_call_id VARCHAR(128) COLLATE utf8mb4_bin,
    parent_event_uuid VARCHAR(36) COLLATE utf8mb4_bin,
    occurred_at DATETIME(6) NOT NULL,
    payload_json JSON NOT NULL,
    payload_hash VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT pk_ac_trace_events PRIMARY KEY (id),
    CONSTRAINT ck_event_sequence CHECK (attempt_no >= 1 AND seq_no >= 1),
    CONSTRAINT ck_event_visit CHECK (node_visit_no IS NULL OR node_visit_no >= 1),
    CONSTRAINT fk_event_parent_scope FOREIGN KEY(execution_id, attempt_no, parent_event_uuid) REFERENCES ac_trace_events (execution_id, attempt_no, event_uuid),
    CONSTRAINT fk_ac_trace_events_execution_id_ac_test_executions FOREIGN KEY(execution_id) REFERENCES ac_test_executions (id),
    CONSTRAINT uq_ac_trace_events_event_uuid UNIQUE (event_uuid),
    CONSTRAINT uq_event_parent_scope UNIQUE (execution_id, attempt_no, event_uuid),
    CONSTRAINT uq_event_sequence UNIQUE (execution_id, attempt_no, seq_no)
)CHARSET=utf8mb4 ENGINE=InnoDB COLLATE utf8mb4_0900_ai_ci;

CREATE INDEX ix_event_cursor ON ac_trace_events (execution_id, id);

DELIMITER $$
CREATE TRIGGER `guard_ac_target_versions_update` BEFORE UPDATE ON `ac_target_versions` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_target_versions_delete` BEFORE DELETE ON `ac_target_versions` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_knowledge_documents_update` BEFORE UPDATE ON `ac_knowledge_documents` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_knowledge_documents_delete` BEFORE DELETE ON `ac_knowledge_documents` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_fault_profiles_update` BEFORE UPDATE ON `ac_fault_profiles` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_fault_profiles_delete` BEFORE DELETE ON `ac_fault_profiles` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_trace_events_update` BEFORE UPDATE ON `ac_trace_events` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_trace_events_delete` BEFORE DELETE ON `ac_trace_events` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_assertion_results_update` BEFORE UPDATE ON `ac_assertion_results` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_assertion_results_delete` BEFORE DELETE ON `ac_assertion_results` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_artifacts_update` BEFORE UPDATE ON `ac_artifacts` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_ac_artifacts_delete` BEFORE DELETE ON `ac_artifacts` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'immutable_revision_or_evidence'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_case_insert` BEFORE INSERT ON `ac_test_cases` FOR EACH ROW BEGIN IF NEW.review_status = 'approved' AND NOT EXISTS (SELECT 1 FROM ac_users WHERE id=NEW.reviewer_id AND role='reviewer' AND enabled=1) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'reviewer_required'; END IF;IF NEW.review_status = 'approved' AND EXISTS (SELECT 1 FROM ac_test_cases WHERE family_key=NEW.family_key AND review_status='approved' AND dataset_split<>NEW.dataset_split) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'approved_family_split_conflict'; END IF; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_case_update` BEFORE UPDATE ON `ac_test_cases` FOR EACH ROW BEGIN IF OLD.review_status='approved' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'approved_case_immutable'; END IF;IF NEW.review_status = 'approved' AND NOT EXISTS (SELECT 1 FROM ac_users WHERE id=NEW.reviewer_id AND role='reviewer' AND enabled=1) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'reviewer_required'; END IF;IF NEW.review_status = 'approved' AND EXISTS (SELECT 1 FROM ac_test_cases WHERE family_key=NEW.family_key AND review_status='approved' AND dataset_split<>NEW.dataset_split) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'approved_family_split_conflict'; END IF; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_case_delete` BEFORE DELETE ON `ac_test_cases` FOR EACH ROW BEGIN IF OLD.review_status='approved' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'approved_case_immutable'; END IF; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_run_config` BEFORE UPDATE ON `ac_test_runs` FOR EACH ROW BEGIN IF NOT (OLD.`run_uuid` <=> NEW.`run_uuid`) OR NOT (OLD.`target_version_id` <=> NEW.`target_version_id`) OR NOT (OLD.`created_by` <=> NEW.`created_by`) OR NOT (OLD.`manifest_json` <=> NEW.`manifest_json`) OR NOT (OLD.`manifest_hash` <=> NEW.`manifest_hash`) OR NOT (OLD.`budget_json` <=> NEW.`budget_json`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'frozen_execution_config'; END IF; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_run_delete` BEFORE DELETE ON `ac_test_runs` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'execution_evidence_retained'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_execution_config` BEFORE UPDATE ON `ac_test_executions` FOR EACH ROW BEGIN IF NOT (OLD.`execution_uuid` <=> NEW.`execution_uuid`) OR NOT (OLD.`run_id` <=> NEW.`run_id`) OR NOT (OLD.`case_revision_id` <=> NEW.`case_revision_id`) OR NOT (OLD.`repeat_no` <=> NEW.`repeat_no`) OR NOT (OLD.`seed` <=> NEW.`seed`) OR NOT (OLD.`scope_id` <=> NEW.`scope_id`) OR NOT (OLD.`session_id` <=> NEW.`session_id`) OR NOT (OLD.`input_snapshot_json` <=> NEW.`input_snapshot_json`) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'frozen_execution_config'; END IF;IF NEW.attempt_no < OLD.attempt_no THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'attempt_cannot_decrease'; END IF; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_execution_delete` BEFORE DELETE ON `ac_test_executions` FOR EACH ROW BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'execution_evidence_retained'; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_execution_insert` BEFORE INSERT ON `ac_test_executions` FOR EACH ROW BEGIN IF NOT EXISTS (SELECT 1 FROM ac_test_cases WHERE id=NEW.case_revision_id AND review_status='approved') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'approved_case_required'; END IF; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_defect_insert` BEFORE INSERT ON `ac_defects` FOR EACH ROW BEGIN IF NOT EXISTS (SELECT 1 FROM ac_test_executions e JOIN ac_test_runs r ON r.id=e.run_id JOIN ac_target_versions v ON v.id=r.target_version_id WHERE e.id=NEW.first_execution_id AND v.target_id=NEW.target_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'defect_target_mismatch'; END IF; END$$
DELIMITER ;

DELIMITER $$
CREATE TRIGGER `guard_defect_update` BEFORE UPDATE ON `ac_defects` FOR EACH ROW BEGIN IF NOT EXISTS (SELECT 1 FROM ac_test_executions e JOIN ac_test_runs r ON r.id=e.run_id JOIN ac_target_versions v ON v.id=r.target_version_id WHERE e.id=NEW.first_execution_id AND v.target_id=NEW.target_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'defect_target_mismatch'; END IF; END$$
DELIMITER ;

INSERT INTO alembic_version (version_num) VALUES ('20261025_0001');

-- Running upgrade 20261025_0001 -> 20261025_0002

ALTER TABLE sb_booking_drafts ADD COLUMN session_id VARCHAR(36) COLLATE utf8mb4_bin;

CREATE INDEX ix_draft_session ON sb_booking_drafts (scope_id, actor_key, session_id, status);

UPDATE alembic_version SET version_num='20261025_0002' WHERE alembic_version.version_num = '20261025_0001';

-- Running upgrade 20261025_0002 -> 20261026_0003

ALTER TABLE ac_test_runs ADD COLUMN event_seq BIGINT UNSIGNED NOT NULL DEFAULT '0';

ALTER TABLE ac_trace_events ADD COLUMN run_id BIGINT UNSIGNED;

ALTER TABLE ac_trace_events ADD COLUMN run_seq_no BIGINT UNSIGNED NOT NULL DEFAULT '0';

DROP TRIGGER guard_ac_trace_events_update;

UPDATE ac_trace_events e JOIN ac_test_executions x ON x.id=e.execution_id SET e.run_id=x.run_id;

UPDATE ac_trace_events e JOIN (SELECT id, ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY id) AS rn FROM ac_trace_events) s ON s.id=e.id SET e.run_seq_no=s.rn;

UPDATE ac_test_runs r JOIN (SELECT run_id, MAX(run_seq_no) AS n FROM ac_trace_events GROUP BY run_id) e ON e.run_id=r.id SET r.event_seq=e.n;

DELIMITER $$
CREATE TRIGGER guard_ac_trace_events_update BEFORE UPDATE ON ac_trace_events FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='immutable_revision_or_evidence'$$
DELIMITER ;

ALTER TABLE ac_trace_events MODIFY run_id BIGINT UNSIGNED NOT NULL;

ALTER TABLE ac_trace_events MODIFY execution_id BIGINT UNSIGNED NULL;

ALTER TABLE ac_trace_events ADD CONSTRAINT fk_trace_run FOREIGN KEY(run_id) REFERENCES ac_test_runs (id);

ALTER TABLE ac_trace_events ADD CONSTRAINT uq_run_event_sequence UNIQUE (run_id, run_seq_no);

CREATE INDEX ix_run_event_cursor ON ac_trace_events (run_id, run_seq_no);

CREATE INDEX ix_run_event_type ON ac_trace_events (run_id, event_type);

CREATE INDEX ix_event_parent ON ac_trace_events (parent_event_uuid);

DELIMITER $$
CREATE TRIGGER guard_trace_insert BEFORE INSERT ON ac_trace_events FOR EACH ROW
BEGIN
 DECLARE owner_run BIGINT UNSIGNED;
 DECLARE current_seq BIGINT UNSIGNED;
 IF NEW.execution_id IS NOT NULL THEN
   SELECT run_id INTO owner_run FROM ac_test_executions WHERE id=NEW.execution_id;
   IF owner_run IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='event_execution_missing'; END IF;
   IF NEW.run_id IS NULL THEN SET NEW.run_id=owner_run; END IF;
   IF NOT (NEW.run_id <=> owner_run) THEN
     SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='event_run_mismatch';
   END IF;
 END IF;
 IF NEW.run_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='event_run_required'; END IF;
 SELECT event_seq INTO current_seq FROM ac_test_runs WHERE id=NEW.run_id FOR UPDATE;
 IF current_seq IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='event_run_missing'; END IF;
 IF NEW.run_seq_no<>0 AND NEW.run_seq_no<>current_seq+1 THEN
   SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='event_sequence_mismatch';
 END IF;
 SET NEW.run_seq_no=current_seq+1;
 UPDATE ac_test_runs SET event_seq=NEW.run_seq_no WHERE id=NEW.run_id;
 IF NEW.execution_id IS NULL THEN
   SET NEW.seq_no=NEW.run_seq_no;
   IF NEW.parent_event_uuid IS NOT NULL AND NOT EXISTS
     (SELECT 1 FROM ac_trace_events p WHERE p.event_uuid=NEW.parent_event_uuid
       AND p.run_id=NEW.run_id AND p.execution_id IS NULL AND p.attempt_no=NEW.attempt_no) THEN
     SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='event_parent_scope';
   END IF;
 END IF;
END$$
DELIMITER ;

UPDATE alembic_version SET version_num='20261026_0003' WHERE alembic_version.version_num = '20261025_0002';

-- Running upgrade 20261026_0003 -> 20261028_0004

ALTER TABLE ac_artifacts MODIFY run_id BIGINT UNSIGNED NULL;

ALTER TABLE ac_artifacts ADD COLUMN owner_id BIGINT UNSIGNED;

ALTER TABLE ac_artifacts ADD CONSTRAINT fk_ac_artifacts_owner_id_ac_users FOREIGN KEY(owner_id) REFERENCES ac_users (id);

CREATE INDEX ix_artifact_owner ON ac_artifacts (owner_id, id);

ALTER TABLE ac_artifacts ADD CONSTRAINT ck_artifact_owner CHECK ((run_id IS NOT NULL AND owner_id IS NULL) OR (run_id IS NULL AND owner_id IS NOT NULL AND kind IN ('casegen_request','casegen_result','casegen_call','case_review')));

UPDATE alembic_version SET version_num='20261028_0004' WHERE alembic_version.version_num = '20261026_0003';

-- Running upgrade 20261028_0004 -> 20261028_0005

ALTER TABLE ac_artifacts DROP CHECK ck_artifact_owner;

ALTER TABLE ac_artifacts ADD CONSTRAINT ck_artifact_owner CHECK ((run_id IS NOT NULL AND owner_id IS NULL) OR (run_id IS NULL AND owner_id IS NOT NULL AND kind IN ('casegen_request','casegen_result','casegen_call','case_review','judge_request','judge_result','judge_call','judge_label','judge_review')));

UPDATE alembic_version SET version_num='20261028_0005' WHERE alembic_version.version_num = '20261028_0004';

-- Running upgrade 20261028_0005 -> 20261028_0006

ALTER TABLE ac_artifacts DROP CHECK ck_artifact_owner;

ALTER TABLE ac_artifacts ADD CONSTRAINT ck_artifact_owner CHECK ((run_id IS NOT NULL AND owner_id IS NULL) OR (run_id IS NULL AND owner_id IS NOT NULL AND kind IN ('casegen_request','casegen_result','casegen_call','case_review','judge_request','judge_result','judge_call','judge_label','judge_review','dataset_manifest')));

UPDATE alembic_version SET version_num='20261028_0006' WHERE alembic_version.version_num = '20261028_0005';

-- Running upgrade 20261028_0006 -> 20261107_0007

DROP TRIGGER IF EXISTS guard_execution_insert;

DELIMITER $$
CREATE TRIGGER guard_execution_insert BEFORE INSERT ON ac_test_executions FOR EACH ROW BEGIN IF NOT (EXISTS (SELECT 1 FROM ac_test_cases WHERE id=NEW.case_revision_id AND review_status='approved') OR EXISTS (
 SELECT 1 FROM ac_test_cases c
 JOIN ac_test_runs r ON r.id=NEW.run_id
 JOIN ac_artifacts a ON a.kind='reduction_revision'
 JOIN ac_test_runs original ON original.id=a.run_id
 WHERE c.id=NEW.case_revision_id AND c.source='reduction_trial:T17M'
 AND c.review_status='draft' AND c.dataset_split='dev'
 AND original.created_by=r.created_by AND original.target_version_id=r.target_version_id
 AND a.generated_by_revision='T17M-v1'
 AND JSON_EXTRACT(a.metadata_json,'$.private_case_id')=c.id
 AND JSON_UNQUOTE(JSON_EXTRACT(a.metadata_json,'$.plan_id'))=
     JSON_UNQUOTE(JSON_EXTRACT(r.manifest_json,'$.workflow.reduction_id'))
 AND JSON_EXTRACT(r.manifest_json,'$.cases[0].case_revision_id')=c.id
 AND JSON_UNQUOTE(JSON_EXTRACT(r.manifest_json,'$.cases[0].content_hash'))=c.content_hash
 AND JSON_LENGTH(JSON_EXTRACT(r.manifest_json,'$.cases'))=1
 AND JSON_CONTAINS(JSON_EXTRACT(r.manifest_json,'$.workflow.reduction_case_ids'),CAST(c.id AS JSON))
)) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='approved_case_required'; END IF; END$$
DELIMITER ;

UPDATE alembic_version SET version_num='20261107_0007' WHERE alembic_version.version_num = '20261028_0006';
