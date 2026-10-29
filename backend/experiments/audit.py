"""Read-only ongoing/final preservation and raw-evidence audit; never settles a ticket."""

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.engine import create_database_engine
from app.models import Artifact, Booking, Defect, TargetVersion, TestCase, TestExecution, TestRun
from experiments.budget import AGENT_AUTH, JUDGE_AUTH
from experiments.common import EVIDENCE, LOCAL, ROOT, read, settings, sha, write_new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-active", action="store_true")
    args = parser.parse_args()
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
    engine = create_database_engine(s)
    formal = create_database_engine(settings())
    try:

        def ledger(key):
            return {k.decode(): int(v) for k, v in redis.hgetall(key).items()}

        assert {k: ledger(k) for k in baseline["ledgers"]} == baseline["ledgers"]
        for relative, digest in baseline["old_files"].items():
            assert sha(ROOT / relative) == digest, relative
        with Session(engine) as session:
            state = {
                m.__tablename__: [
                    {c.name: getattr(row, c.name) for c in m.__table__.columns}
                    for row in session.scalars(select(m).order_by(m.id))
                ]
                for m in [
                    TargetVersion,
                    TestCase,
                    Artifact,
                    Defect,
                    TestRun,
                    TestExecution,
                    Booking,
                ]
            }
        assert json.loads(json.dumps(state, default=str)) == baseline["database"]
        private = read(LOCAL / "session-private.json")
        manifests = {
            "V0": read(ROOT / "docs/evidence/T13/frozen-source-v1.1-manifest.json"),
            "V1": read(EVIDENCE / "v1-candidate-manifest.json"),
        }
        for version, manifest in manifests.items():
            directory = Path(private["snapshots"][version])
            assert all(sha(directory / p) == digest for p, digest in manifest["files"].items())
        protocol = read(EVIDENCE / "E1-protocol-freeze.json")
        assert sha(ROOT / "docs/fixtures/T16/E1-protocol.md") == protocol["protocol_sha256"]
        assert sha(ROOT / "backend/experiments/e1.py") == protocol["analysis_source_sha256"]
        verified, providers = 0, 0
        for cohort in ("episodes", "serial-episodes"):
            for outcome in (EVIDENCE / cohort).glob("*/outcome.json"):
                directory = outcome.parent
                events = read(directory / "events.json")
                run = read(directory / "run.json")
                assert len(events) == run["event_count"]
                assert [x["event"]["run_seq"] for x in events] == list(range(1, len(events) + 1))
                if (directory / "provider-manifest.json").exists():
                    for entry in read(directory / "provider-manifest.json"):
                        assert sha(directory / entry["path"]) == entry["sha256"]
                        providers += 1
                else:
                    assert cohort == "episodes", "Formal export must have provider manifest"
                    archive = read(EVIDENCE / "diagnostic-provider-archive/manifest.json")
                    pilot = next(x for x in archive if x["episode"] == directory.name)
                    for entry in pilot["files"]:
                        assert sha(EVIDENCE / entry["path"]) == entry["sha256"]
                        providers += 1
                plan = read(directory / "plan.json")
                # Pilot common/episodes predate the documented serial amendment.
                # Only the formal serial cohort must match the current execution harness.
                if cohort == "serial-episodes":
                    for name in ("budget.py", "common.py", "episodes.py"):
                        assert sha(ROOT / "backend/experiments" / name) == plan["harness"][name]
                verified += 1
        with Session(formal) as session:
            active = list(
                session.scalars(select(TestExecution).where(TestExecution.status == "running"))
            )
            statuses = {}
            for execution in session.scalars(select(TestExecution)):
                statuses[execution.status] = statuses.get(execution.status, 0) + 1
        if not args.allow_active:
            assert not active, "Formal execution still active; use ongoing audit mode"
        output = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "mode": "ongoing" if args.allow_active else "no_running_execution",
            "main_database_unchanged": True,
            "main_paid_flags_false": True,
            "old_files_unchanged": len(baseline["old_files"]),
            "old_ledgers_unchanged": len(baseline["ledgers"]),
            "frozen_source_archives_unchanged": ["V0", "V1"],
            "execution_harness_unchanged": ["budget.py", "common.py", "episodes.py"],
            "harness_comparison_scope": (
                "Formal serial only; diagnostic pilot predates serial amendment."
            ),
            "exported_episodes_verified": verified,
            "provider_files_verified": providers,
            "formal_database_status_counts_including_diagnostic": statuses,
            "role_ledgers": {
                role: ledger(f"ac:t16:budget:{auth}:{role}")
                for role, auth in (("agent", AGENT_AUTH), ("judge", JUDGE_AUTH))
            },
            "c_free_bytes": shutil.disk_usage("C:/").free,
            "no_ticket_or_database_mutation": True,
        }
        path = EVIDENCE / "audits" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.json")
        write_new(path, output)
        print(json.dumps(output))
    finally:
        formal.dispose()
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
