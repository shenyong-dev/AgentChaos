"""Append persisted rule rows as sidecars, without touching frozen execution/export code."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import create_database_engine
from app.models import AssertionResult, TestExecution
from experiments.common import EVIDENCE, read, settings, sha, write_new


def main():
    engine = create_database_engine(settings())
    files, count = 0, 0
    try:
        with Session(engine) as session:
            for cohort in ("episodes", "serial-episodes"):
                for outcome in sorted((EVIDENCE / cohort).glob("*/outcome.json")):
                    value = read(outcome)
                    execution = session.scalar(
                        select(TestExecution).where(
                            TestExecution.execution_uuid == value["execution_id"]
                        )
                    )
                    assert execution
                    rows = list(
                        session.scalars(
                            select(AssertionResult)
                            .where(AssertionResult.execution_id == execution.id)
                            .order_by(AssertionResult.id)
                        )
                    )
                    data = {
                        "execution_id": value["execution_id"],
                        "source_run_sha256": sha(outcome.parent / "run.json"),
                        "rows": [
                            {c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows
                        ],
                        "no_re_evaluation_or_model_calls": True,
                    }
                    path = EVIDENCE / "assertion-results" / cohort / (outcome.parent.name + ".json")
                    if path.exists():
                        assert read(path) == json.loads(json.dumps(data, default=str)), str(path)
                    else:
                        write_new(path, data)
                    files += 1
                    count += len(rows)
        print(
            json.dumps(
                {"episode_sidecars": files, "persisted_assertion_rows": count, "new_model_calls": 0}
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
