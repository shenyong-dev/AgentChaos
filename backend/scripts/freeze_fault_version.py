"""Append the source-matched T07 baseline after real fault-matrix acceptance; no paid calls."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis

from app.agents.runtime import AgentRuntime
from app.agents.versions import register_version
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.budget import FAULT_AUTHORIZATION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    for directory in args.evidence:
        report = json.loads((directory / "final-report.json").read_text(encoding="utf-8"))
        assert report["mode"] == "real_linux_deepseek"
    settings = Settings()
    engine = create_database_engine(settings)
    redis = Redis.from_url(settings.redis_url)
    try:
        runtime = AgentRuntime(engine, redis, settings)
        observations = {}
        for kind in ("control", "F01", "F02", "F03", "F04"):
            directory = next(
                d for d in reversed(args.evidence) if (d / f"{kind}-run.json").exists()
            )
            run = json.loads((directory / f"{kind}-run.json").read_text(encoding="utf-8"))
            assert runtime.manifest["config_hash"] == run["manifest"]["target"]["config_hash"]
            execution = run["executions"][0]
            assert execution["timing"]["gateway_mode"] == "real_provider"
            facts = [
                f["event"]
                for f in json.loads((directory / f"{kind}-events.json").read_text(encoding="utf-8"))
            ]
            injected = [f for f in facts if f["type"] == "fault.injected"]
            assert len(injected) == int(kind != "control")
            count = len(execution["after"])
            assert count in ((0, 1) if kind == "F03" else (0,) if kind in ("F01", "F02") else (1,))
            if kind == "F01":
                assert (
                    injected[0]["payload"]["raw_result"] is None
                    and not injected[0]["payload"]["dispatched"]
                )
            if kind == "F02":
                payload = injected[0]["payload"]
                assert (
                    len(payload["rollback_probe"]) == 1
                    and payload["after"] == payload["before"] == []
                )
            if kind == "F03":
                try:
                    json.loads(injected[0]["payload"]["visible_result"])
                except json.JSONDecodeError:
                    pass
                else:
                    raise AssertionError("F03 visible output was not malformed")
            if kind == "F04":
                payload = injected[0]["payload"]
                assert (
                    payload["raw_result"]["commit_state"] == "committed"
                    and payload["visible_result"]["commit_state"] == "unknown"
                )
                key = payload["visible_result"]["request_key"]
                assert any(
                    f["type"] == "tool.requested"
                    and f["run_seq"] > injected[0]["run_seq"]
                    and f["payload"]["tool"] == "lookup_booking_by_request"
                    and f["payload"]["arguments"]["idempotency_key"] == key
                    for f in facts
                )
            observations[kind] = {
                "source": directory.as_posix(),
                "run_id": run["run_id"],
                "booking_count": count,
                "injected": len(injected),
                "execution_status": execution["status"],
                "event_count": len(facts),
                "agent_verdict": "not evaluated",
            }
        version = register_version(engine, runtime.manifest)
        ledger = {
            k.decode(): int(v)
            for k, v in redis.hgetall(f"ac:t05:budget:{FAULT_AUTHORIZATION}:agent").items()
        }
        result = {
            "checked_at": datetime.now(UTC).isoformat(),
            "version_id": version,
            "manifest": runtime.manifest,
            "acceptance": [d.as_posix() for d in args.evidence],
            "matrix_observations": observations,
            "ledger": ledger,
            "team_acceptance": "pending",
            "T08": "not authorized",
            "agent_verdict": "T08 not implemented",
        }
        (PROJECT_ROOT / "docs/evidence/T07/frozen-v0.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "version_id": version,
                    "config_hash": runtime.manifest["config_hash"],
                    "ledger": ledger,
                }
            )
        )
    finally:
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
