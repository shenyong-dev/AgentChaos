"""Permit only owner-bound personal external case execution without a review."""

from alembic.script import ScriptDirectory
from sqlalchemy import text

from alembic import op

revision = "20261119_0008"
down_revision = "20261107_0007"
branch_labels = None
depends_on = None

PERSONAL = """EXISTS (
 SELECT 1 FROM ac_test_cases c
 JOIN ac_test_runs r ON r.id=NEW.run_id
 JOIN ac_target_versions v ON v.id=r.target_version_id
 JOIN ac_targets t ON t.id=v.target_id
 WHERE c.id=NEW.case_revision_id
 AND c.review_status='draft' AND c.dataset_split='dev'
 AND t.owner_id=r.created_by AND t.archived_at IS NULL
 AND REGEXP_LIKE(t.adapter_type,'^external_http:user-[0-9a-f]{32}$','c')
 AND c.source=CONCAT('external_import:',t.adapter_type,':owner:',r.created_by)
 AND JSON_UNQUOTE(JSON_EXTRACT(c.fixture_json,'$.fixture'))='external-dialogue-v1'
 AND JSON_UNQUOTE(JSON_EXTRACT(c.fixture_json,'$.adapter_type'))=t.adapter_type
 AND JSON_UNQUOTE(JSON_EXTRACT(r.manifest_json,'$.adapter_type'))=t.adapter_type
 AND JSON_CONTAINS(
     JSON_EXTRACT(r.manifest_json,'$.cases'),
     JSON_OBJECT('case_revision_id',c.id,'content_hash',c.content_hash)
 )
 AND JSON_EXTRACT(NEW.input_snapshot_json,'$.case_revision_id')=c.id
 AND JSON_UNQUOTE(JSON_EXTRACT(NEW.input_snapshot_json,'$.content_hash'))=c.content_hash
)"""


def previous_rule():
    scripts = ScriptDirectory.from_config(op.get_context().config)
    return scripts.get_revision(down_revision).module


def upgrade():
    previous = previous_rule()
    try:
        previous.install(previous.NORMAL + " OR " + previous.PRIVATE + " OR " + PERSONAL)
    except Exception:
        previous.install(previous.NORMAL + " OR " + previous.PRIVATE)
        raise


def downgrade():
    if op.get_bind().scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM ac_test_executions e JOIN ac_test_cases c "
            "ON c.id=e.case_revision_id WHERE "
            "REGEXP_LIKE(c.source,'^external_import:external_http:user-[0-9a-f]{32}:owner:[0-9]+$','c'))"
        )
    ):
        raise RuntimeError("Personal Agent execution evidence exists; keep the execution rule")
    previous = previous_rule()
    previous.install(previous.NORMAL + " OR " + previous.PRIVATE)
