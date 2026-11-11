"""T17M explicitly fenced private draft executions; public approval gate remains."""

from sqlalchemy import text

from alembic import op

revision = "20261107_0007"
down_revision = "20261028_0006"
branch_labels = None
depends_on = None

NORMAL = "EXISTS (SELECT 1 FROM ac_test_cases WHERE id=NEW.case_revision_id AND review_status='approved')"
PRIVATE = """EXISTS (
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
)"""


def install(expression):
    op.execute("DROP TRIGGER IF EXISTS guard_execution_insert")
    op.execute(
        "CREATE TRIGGER guard_execution_insert BEFORE INSERT ON ac_test_executions "
        "FOR EACH ROW BEGIN IF NOT (" + expression + ") THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='approved_case_required'; END IF; END"
    )


def upgrade():
    install(NORMAL + " OR " + PRIVATE)


def downgrade():
    if op.get_bind().scalar(
        text(
            "SELECT COUNT(*) FROM ac_test_executions e JOIN ac_test_cases c "
            "ON c.id=e.case_revision_id WHERE c.source='reduction_trial:T17M'"
        )
    ):
        raise RuntimeError("Private trial evidence exists; never delete it implicitly")
    install(NORMAL)
