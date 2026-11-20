"""Persist login audit events without touching active sessions or account rows."""

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME

from alembic import op

revision = "20261120_0011"
down_revision = "20261119_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ac_login_events",
        sa.Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column("user_id", BIGINT(unsigned=True)),
        sa.Column("username", sa.String(64, collation="utf8mb4_bin")),
        sa.Column("role", sa.String(16)),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32)),
        sa.Column("ip_address", sa.String(45, collation="utf8mb4_bin")),
        sa.Column("location", sa.String(180)),
        sa.Column("location_source", sa.String(16)),
        sa.Column("user_agent", sa.String(384)),
        sa.Column("request_id", sa.String(36, collation="utf8mb4_bin")),
        sa.CheckConstraint(
            "outcome IN ('success','failure','limited')", name="ck_login_event_outcome"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_login_event_created_at", "ac_login_events", ["created_at"])
    op.create_index("ix_login_event_account", "ac_login_events", ["user_id", "id"])


def downgrade():
    op.drop_table("ac_login_events")
