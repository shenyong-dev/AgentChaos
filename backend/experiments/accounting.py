"""Reconcile SQL tickets and persistent role ledgers, without modifying either."""

import argparse
import json
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import create_database_engine
from app.judging import evidence
from app.models import Artifact, TestExecution
from experiments.budget import AGENT_AUTH, JUDGE_AUTH
from experiments.common import EVIDENCE, settings, write_new


def totals(tickets):
    result = dict.fromkeys(
        ("calls_started", "calls_completed", "held_upper", "charged_estimate", "token_upper"), 0
    )
    for ticket in tickets.values():
        result["calls_started"] += 1
        if ticket["state"] == "settled":
            result["calls_completed"] += 1
            result["charged_estimate"] += ticket["estimate_microusd"]
            result["token_upper"] += (
                ticket["usage"]["input_tokens"] + ticket["usage"]["output_tokens"]
            )
        else:
            result["held_upper"] += ticket["upper_microusd"]
            result["token_upper"] += ticket["input_token_upper"] + ticket["max_output_tokens"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-active", action="store_true")
    args = parser.parse_args()
    s = settings()
    redis = Redis.from_url(s.redis_url)
    engine = create_database_engine(s)
    keys = {
        role: f"ac:t16:budget:{auth}:{role}"
        for role, auth in (("agent", AGENT_AUTH), ("judge", JUDGE_AUTH))
    }

    def snapshot():
        return {
            role: {k.decode(): int(v) for k, v in redis.hgetall(key).items()}
            for role, key in keys.items()
        }

    try:
        before = snapshot()
        tickets = {"agent": {}, "judge": {}}
        with Session(engine) as session:
            active = False
            for execution in session.scalars(select(TestExecution)):
                active |= execution.status == "running"
                for identifier, ticket in (execution.usage_json or {}).get("tickets", {}).items():
                    assert identifier not in tickets["agent"]
                    tickets["agent"][identifier] = ticket
            for row in session.scalars(
                select(Artifact).where(Artifact.kind == "judge_call").order_by(Artifact.id)
            ):
                value = evidence.read(row)
                ticket = value.get("ticket")
                if not ticket:
                    continue
                identifier = ticket["call_id"]
                phase = row.metadata_json["phase"]
                if phase == "reserved":
                    assert identifier not in tickets["judge"]
                    tickets["judge"][identifier] = {**ticket, "state": "reserved"}
                elif phase in {"settled", "unknown"}:
                    assert identifier in tickets["judge"]
                    tickets["judge"][identifier].update(value, state=phase)
        after = snapshot()
        expected = {role: totals(values) for role, values in tickets.items()}
        differences = {
            role: {
                k: {"SQL": v, "Redis": after[role].get(k, 0)}
                for k, v in values.items()
                if v != after[role].get(k, 0)
            }
            for role, values in expected.items()
        }
        stable = before == after and not active
        output = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "stable_no_running_agent_snapshot": stable,
            "SQL_ticket_totals": expected,
            "Redis_role_ledgers": after,
            "differences": differences,
            "not_an_invoice": True,
            "no_settlement_or_mutation": True,
        }
        write_new(
            EVIDENCE / "accounting" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.json"), output
        )
        print(json.dumps(output))
        if not args.allow_active:
            assert stable and not any(differences.values()), (
                "Unstable or unreconciled accounting; inspect receipt"
            )
    finally:
        engine.dispose()
        redis.close()


if __name__ == "__main__":
    main()
