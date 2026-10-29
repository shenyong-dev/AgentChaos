"""T13 immutable owner-bound dataset approval; same sixteen domain tables."""

import sqlalchemy as sa

from alembic import op

revision = "20261028_0006"
down_revision = "20261028_0005"
branch_labels = None
depends_on = None
OLD = "'casegen_request','casegen_result','casegen_call','case_review','judge_request','judge_result','judge_call','judge_label','judge_review'"


def expression(kinds):
    return (
        "(run_id IS NOT NULL AND owner_id IS NULL) OR (run_id IS NULL AND owner_id IS NOT NULL AND kind IN ("
        + kinds
        + "))"
    )


def upgrade():
    op.drop_constraint("ck_artifact_owner", "ac_artifacts", type_="check")
    op.create_check_constraint(
        "ck_artifact_owner", "ac_artifacts", expression(OLD + ",'dataset_manifest'")
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM ac_artifacts WHERE run_id IS NULL AND kind='dataset_manifest'"
        )
    ):
        raise RuntimeError("Dataset evidence exists; never delete it implicitly")
    op.drop_constraint("ck_artifact_owner", "ac_artifacts", type_="check")
    op.create_check_constraint("ck_artifact_owner", "ac_artifacts", expression(OLD))
