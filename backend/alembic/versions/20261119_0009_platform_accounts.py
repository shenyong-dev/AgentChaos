"""Add administrator and basic accounts without replacing existing users."""

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT

from alembic import op

revision = "20261119_0009"
down_revision = "20261119_0008"
branch_labels = None
depends_on = None


def case_guards(reviewers):
    approval = (
        "IF NEW.review_status='approved' AND NOT EXISTS "
        "(SELECT 1 FROM ac_users WHERE id=NEW.reviewer_id "
        f"AND role IN ({reviewers}) AND enabled=1) "
        "THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='reviewer_required'; END IF;"
        "IF NEW.review_status='approved' AND EXISTS "
        "(SELECT 1 FROM ac_test_cases WHERE family_key=NEW.family_key AND review_status='approved' "
        "AND dataset_split<>NEW.dataset_split) "
        "THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='approved_family_split_conflict'; END IF;"
    )
    immutable = (
        "IF OLD.review_status='approved' "
        "THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='approved_case_immutable'; END IF;"
    )
    for trigger, operation, guard in (
        ("guard_case_insert", "INSERT", ""),
        ("guard_case_update", "UPDATE", immutable),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE {operation} ON ac_test_cases "
            f"FOR EACH ROW BEGIN {guard}{approval} END"
        )


def upgrade():
    op.add_column(
        "ac_users",
        sa.Column("auth_epoch", BIGINT(unsigned=True), nullable=False, server_default=sa.text("0")),
    )
    op.execute("ALTER TABLE ac_users DROP CHECK ck_users_role")
    op.create_check_constraint(
        "ck_users_role", "ac_users", "role IN ('admin','operator','reviewer','user')"
    )
    case_guards("'reviewer','admin'")


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM ac_users WHERE role IN ('admin','user')")
    ):
        raise RuntimeError("Managed accounts exist; downgrade would invalidate their roles")
    case_guards("'reviewer'")
    op.execute("ALTER TABLE ac_users DROP CHECK ck_users_role")
    op.create_check_constraint("ck_users_role", "ac_users", "role IN ('operator','reviewer')")
    op.drop_column("ac_users", "auth_epoch")
