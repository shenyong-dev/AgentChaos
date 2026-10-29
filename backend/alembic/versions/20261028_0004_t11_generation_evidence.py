"""T11 owner-bound immutable evidence without inventing Agent test runs.

Revision ID: 20261028_0004
Revises: 20261026_0003
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "20261028_0004"
down_revision = "20261026_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "ac_artifacts", "run_id", existing_type=mysql.BIGINT(unsigned=True), nullable=True
    )
    op.add_column("ac_artifacts", sa.Column("owner_id", mysql.BIGINT(unsigned=True), nullable=True))
    op.create_foreign_key(
        "fk_ac_artifacts_owner_id_ac_users", "ac_artifacts", "ac_users", ["owner_id"], ["id"]
    )
    op.create_index("ix_artifact_owner", "ac_artifacts", ["owner_id", "id"])
    op.create_check_constraint(
        "ck_artifact_owner",
        "ac_artifacts",
        "(run_id IS NOT NULL AND owner_id IS NULL) OR "
        "(run_id IS NULL AND owner_id IS NOT NULL AND kind IN "
        "('casegen_request','casegen_result','casegen_call','case_review'))",
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM ac_artifacts WHERE run_id IS NULL")):
        raise RuntimeError(
            "Archive owner-bound generation/review evidence before downgrade; never delete it implicitly"
        )
    op.drop_constraint("ck_artifact_owner", "ac_artifacts", type_="check")
    op.drop_constraint("fk_ac_artifacts_owner_id_ac_users", "ac_artifacts", type_="foreignkey")
    op.drop_index("ix_artifact_owner", "ac_artifacts")
    op.drop_column("ac_artifacts", "owner_id")
    op.alter_column(
        "ac_artifacts", "run_id", existing_type=mysql.BIGINT(unsigned=True), nullable=False
    )
