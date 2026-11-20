"""Persist the global and account-level permission for real Agent model calls."""

import sqlalchemy as sa

from alembic import op

revision = "20261119_0010"
down_revision = "20261119_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ac_users",
        sa.Column("model_calls_enabled", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE ac_users SET model_calls_enabled = 1 WHERE role IN ('admin', 'operator')")
    op.create_table(
        "ac_model_call_policy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_model_call_policy_singleton"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.execute("INSERT INTO ac_model_call_policy (id, enabled) VALUES (1, 0)")


def downgrade():
    op.drop_table("ac_model_call_policy")
    op.drop_column("ac_users", "model_calls_enabled")
