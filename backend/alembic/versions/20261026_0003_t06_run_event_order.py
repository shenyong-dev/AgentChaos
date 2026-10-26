"""T06 real run-level events and transactionally allocated per-run order.

Revision ID: 20261026_0003
Revises: 20261025_0002
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "20261026_0003"
down_revision = "20261025_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ac_test_runs",
        sa.Column("event_seq", mysql.BIGINT(unsigned=True), nullable=False, server_default="0"),
    )
    op.add_column(
        "ac_trace_events", sa.Column("run_id", mysql.BIGINT(unsigned=True), nullable=True)
    )
    op.add_column(
        "ac_trace_events",
        sa.Column("run_seq_no", mysql.BIGINT(unsigned=True), nullable=False, server_default="0"),
    )
    # Controlled migration only: immutable update protection is immediately restored.
    op.execute("DROP TRIGGER guard_ac_trace_events_update")
    op.execute(
        "UPDATE ac_trace_events e JOIN ac_test_executions x ON x.id=e.execution_id "
        "SET e.run_id=x.run_id"
    )
    op.execute(
        "UPDATE ac_trace_events e JOIN (SELECT id, ROW_NUMBER() OVER "
        "(PARTITION BY run_id ORDER BY id) AS rn FROM ac_trace_events) s ON s.id=e.id "
        "SET e.run_seq_no=s.rn"
    )
    op.execute(
        "UPDATE ac_test_runs r JOIN (SELECT run_id, MAX(run_seq_no) AS n "
        "FROM ac_trace_events GROUP BY run_id) e ON e.run_id=r.id SET r.event_seq=e.n"
    )
    _immutable_update()
    op.alter_column(
        "ac_trace_events", "run_id", existing_type=mysql.BIGINT(unsigned=True), nullable=False
    )
    op.alter_column(
        "ac_trace_events", "execution_id", existing_type=mysql.BIGINT(unsigned=True), nullable=True
    )
    op.create_foreign_key("fk_trace_run", "ac_trace_events", "ac_test_runs", ["run_id"], ["id"])
    op.create_unique_constraint(
        "uq_run_event_sequence", "ac_trace_events", ["run_id", "run_seq_no"]
    )
    op.create_index("ix_run_event_cursor", "ac_trace_events", ["run_id", "run_seq_no"])
    op.create_index("ix_run_event_type", "ac_trace_events", ["run_id", "event_type"])
    op.create_index("ix_event_parent", "ac_trace_events", ["parent_event_uuid"])
    op.execute(
        sa.text("""
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
END
""")
    )


def _immutable_update():
    op.execute(
        sa.text(
            "CREATE TRIGGER guard_ac_trace_events_update BEFORE UPDATE ON "
            "ac_trace_events FOR EACH ROW SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='immutable_revision_or_evidence'"
        )
    )


def downgrade():
    # Never silently erase evidence to force an incompatible downgrade.
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM ac_trace_events WHERE execution_id IS NULL")
    ):
        raise RuntimeError(
            "Run-level evidence exists; export and review before incompatible downgrade"
        )
    op.execute("DROP TRIGGER guard_trace_insert")
    op.drop_constraint("fk_trace_run", "ac_trace_events", type_="foreignkey")
    op.drop_index("ix_run_event_type", "ac_trace_events")
    op.drop_index("ix_event_parent", "ac_trace_events")
    op.drop_index("ix_run_event_cursor", "ac_trace_events")
    op.drop_constraint("uq_run_event_sequence", "ac_trace_events", type_="unique")
    op.alter_column(
        "ac_trace_events", "execution_id", existing_type=mysql.BIGINT(unsigned=True), nullable=False
    )
    op.drop_column("ac_trace_events", "run_seq_no")
    op.drop_column("ac_trace_events", "run_id")
    op.drop_column("ac_test_runs", "event_seq")
