"""T12 allow owner-bound semantic calibration evidence, preserving sixteen tables.

Revision ID: 20261028_0005
Revises: 20261028_0004
"""

import sqlalchemy as sa

from alembic import op

revision = "20261028_0005"
down_revision = "20261028_0004"
branch_labels = None
depends_on = None
OLD = "'casegen_request','casegen_result','casegen_call','case_review'"
NEW = OLD + ",'judge_request','judge_result','judge_call','judge_label','judge_review'"


def constraint(kinds):
    return (
        "(run_id IS NOT NULL AND owner_id IS NULL) OR "
        "(run_id IS NULL AND owner_id IS NOT NULL AND kind IN (" + kinds + "))"
    )


def upgrade():
    op.drop_constraint("ck_artifact_owner", "ac_artifacts", type_="check")
    op.create_check_constraint("ck_artifact_owner", "ac_artifacts", constraint(NEW))


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM ac_artifacts WHERE run_id IS NULL AND kind LIKE 'judge_%'")
    ):
        raise RuntimeError("Archive judge evidence before downgrade; never remove it implicitly")
    op.drop_constraint("ck_artifact_owner", "ac_artifacts", type_="check")
    op.create_check_constraint("ck_artifact_owner", "ac_artifacts", constraint(OLD))
