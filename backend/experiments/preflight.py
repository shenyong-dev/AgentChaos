"""Read-only formal checks and non-billable /models probe; no chat completion."""

import json
import shutil
from datetime import UTC, datetime

import httpx
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.engine import create_database_engine
from app.models import Artifact, Booking, Defect, TargetVersion, TestCase, TestExecution, TestRun
from experiments.budget import AGENT_AUTH, AGENT_CAP, JUDGE_AUTH, JUDGE_CAP
from experiments.common import EVIDENCE, LOCAL, ROOT, read, sha, write_new


def main():
    s = Settings()
    assert not any(
        (
            s.allow_paid_model_calls,
            s.executor_allow_paid_model_calls,
            s.casegen_allow_paid_model_calls,
            s.judge_allow_paid_model_calls,
        )
    )
    baseline = read(EVIDENCE / "preparation.json")
    redis = Redis.from_url(s.redis_url)

    def ledger(key):
        return {k.decode(): int(v) for k, v in redis.hgetall(key).items()}

    assert {k: ledger(k) for k in baseline["ledgers"]} == baseline["ledgers"]
    assert ledger(f"ac:t16:budget:{AGENT_AUTH}:agent") == {"limit": AGENT_CAP}
    assert ledger(f"ac:t16:budget:{JUDGE_AUTH}:judge") == {"limit": JUDGE_CAP}
    for relative, digest in baseline["old_files"].items():
        assert sha(ROOT / relative) == digest, relative
    engine = create_database_engine(s)
    with Session(engine) as session:
        state = {
            m.__tablename__: [
                {c.name: getattr(row, c.name) for c in m.__table__.columns}
                for row in session.scalars(select(m).order_by(m.id))
            ]
            for m in [TargetVersion, TestCase, Artifact, Defect, TestRun, TestExecution, Booking]
        }
    assert json.loads(json.dumps(state, default=str)) == baseline["database"]
    parent = read(ROOT / "docs/evidence/T13/frozen-source-v1.1-manifest.json")
    candidate = read(EVIDENCE / "v1-candidate-manifest.json")
    for name, files in [("V0", parent["files"]), ("V1", candidate["files"])]:
        directory = __import__("pathlib").Path(
            read(LOCAL / "session-private.json")["snapshots"][name]
        )
        assert all(sha(directory / p) == digest for p, digest in files.items())
        assert len(read(EVIDENCE / (name + "-bindings.json"))) == 12
    response = httpx.get(
        s.deepseek_base_url + "/models",
        headers={
            "Authorization": "Bearer " + s.deepseek_api_key.get_secret_value(),
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise SystemExit(
            "Non-billable model catalog probe failed: HTTP " + str(response.status_code)
        )
    models = [m["id"] for m in response.json()["data"]]
    assert s.agent_model in models and s.judge_model in models
    assert shutil.disk_usage("C:/").free > 1_500_000_000
    write_new(
        EVIDENCE / "preflight.json",
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "model_list_status": 200,
            "agent_model": s.agent_model,
            "judge_model": s.judge_model,
            "old_files_unchanged": len(baseline["old_files"]),
            "old_ledgers_unchanged": 8,
            "main_database_unchanged": True,
            "source_profiles_verified": {"V0": 12, "V1": 12},
            "new_ledgers_calls_started": 0,
            "main_paid_flags_false": True,
            "c_free_bytes": shutil.disk_usage("C:/").free,
            "global_formal_concurrency": 2,
            "per_run_concurrency": 1,
            "budget_adapter_scope": (
                "Only new authorization/caps; inherited settlement/unknown handling "
                "and original frozen execution function."
            ),
            "human_calibration_gate": "Before judge; real independent labels not yet collected",
        },
    )
    engine.dispose()
    redis.close()
    print(json.dumps({"read_only_preflight": "passed", "model_catalog": 200, "paid_calls": 0}))


if __name__ == "__main__":
    main()
