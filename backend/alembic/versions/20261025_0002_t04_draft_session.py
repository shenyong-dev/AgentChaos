"""T04 bind pending drafts to a trusted conversation without adding tables.

Revision ID: 20261025_0002
Revises: 20261025_0001
"""

import sqlalchemy as sa

from alembic import op

revision = "20261025_0002"
down_revision = "20261025_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sb_booking_drafts",
        sa.Column("session_id", sa.String(36, collation="utf8mb4_bin"), nullable=True),
    )
    op.create_index(
        "ix_draft_session", "sb_booking_drafts", ["scope_id", "actor_key", "session_id", "status"]
    )


def downgrade():
    op.drop_index("ix_draft_session", "sb_booking_drafts")
    op.drop_column("sb_booking_drafts", "session_id")
