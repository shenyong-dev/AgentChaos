"""Register T17B sources while preserving all prior main versions and run records."""

import json
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.models import TargetVersion


def rows(engine, ids=None):
    with Session(engine) as session:
        query = select(TargetVersion).order_by(TargetVersion.id)
        if ids is not None:
            query = query.where(TargetVersion.id.in_(ids))
        return [
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
            for row in session.scalars(query)
        ]


def main():
    output = PROJECT_ROOT / "docs/evidence/T17B/main-version.json"
    assert not output.exists()
    settings = Settings()
    assert not any(
        getattr(settings, key)
        for key in (
            "allow_paid_model_calls",
            "executor_allow_paid_model_calls",
            "casegen_allow_paid_model_calls",
            "judge_allow_paid_model_calls",
        )
    )
    engine = create_database_engine(settings)
    client = Redis.from_url(settings.redis_url)
    try:
        prior = rows(engine)
        prior_ids = [row["id"] for row in prior]
        baseline = json.loads((output.parent / "preparation.json").read_bytes())
        prior_hash = canonical_hash(json.loads(json.dumps(prior, default=str)))
        assert prior_hash == baseline["tables"]["ac_target_versions"]["sha256"]
        runtime = AgentRuntime(engine, client, settings, auto_register=True)
        assert runtime.version_id not in prior_ids
        assert rows(engine, prior_ids) == prior
        result = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": runtime.version_id,
            "config_hash": runtime.manifest["config_hash"],
            "old_version_ids": prior_ids,
            "old_versions_unchanged": True,
            "kind": (
                "judge-only JSON transport change; source compatibility, no new Agent experiment"
            ),
            "new_model_calls": 0,
        }
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result))
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
