"""Bounded crossed-order pairs over unchanged production fault mechanisms."""

import argparse
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content
from app.faults.service import FaultService
from app.models import TestCase, User
from app.reports.service import ReportService
from app.schemas.faults import FaultSpec
from app.schemas.reports import ExportReport
from app.schemas.runs import CreateRun, Limits, RoleBudgets
from benchmarks.t17l.common import disk_guard, write_json
from benchmarks.t17l.lab import Lab

from .budget import BenchmarkBudget, initialize_ledger
from .common import AUTHORIZATION, LEDGER, PROPOSAL, binding
from .offline import OfflineGateway


def required_approval():
    return {
        "authorization": AUTHORIZATION,
        "agent_microusd": 1_500_000,
        "max_requests": 240,
        "max_executions": 12,
        "binding": binding(),
        "human_approved": True,
    }


def execute_batch(output, *, real=False, approval=None):
    frozen = binding()
    catalog = json.loads(PROPOSAL.read_bytes())
    if catalog["hash"] != canonical_hash({k: v for k, v in catalog.items() if k != "hash"}):
        raise RuntimeError("Catalog changed")
    if real:
        if approval != required_approval():
            raise RuntimeError("T17N exact binding and independent budget approval required")
        disk_guard()
        with (PROJECT_ROOT / ".local/t17n/real-started.json").open("x", encoding="utf-8") as f:
            json.dump({"at": datetime.now(UTC).isoformat(), "binding": frozen}, f)
    lab = Lab(output, real=real)
    lab.settings = lab.settings.model_copy(
        update={
            "executor_budget_authorization_id": AUTHORIZATION,
            "executor_agent_budget_usd": 1.5,
        }
    )
    redis = Redis.from_url(lab.settings.redis_url, socket_timeout=15)
    records, comparisons = [], []
    write_json(output / "binding.json", frozen)
    write_json(output / "approval.json", approval if real else {"offline_test_double": True})
    try:
        if real:
            initialize_ledger(redis)
        runtime = AgentRuntime(
            lab.engine,
            redis,
            lab.settings,
            auto_register=True,
            prefix=lab.settings.executor_namespace + ":session",
        )
        write_json(output / "target.json", runtime.manifest)
        with Session(lab.engine) as session, session.begin():
            reviewer = session.scalar(select(User.id).where(User.role == "reviewer"))
            case = TestCase(
                case_key="T17N-PAIRED",
                revision=1,
                family_key="T17N-development-fault-pairs",
                category="robustness",
                dataset_split="dev",
                source="T17N-approved-experiment-input",
                turns_json=catalog["turns"],
                fixture_json={
                    "fixture": "booking-default-v1",
                    "reference_clock": catalog["reference_clock"],
                },
                expected_json=catalog["expected"],
                review_status="approved",
                reviewer_id=reviewer,
                reviewed_at=datetime.now(UTC),
                content_hash="0" * 64,
            )
            case.content_hash = canonical_hash(case_content(case))
            session.add(case)
            session.flush()
            case_id = case.id
        profiles = {
            g["id"]: FaultService(lab.engine).create(
                FaultSpec(
                    profile_key="T17N-" + g["type"],
                    fault_type=g["type"],
                    trigger_stage=g["stage"],
                    target_tool=g["tool"],
                    seed=14001,
                )
            )["id"]
            for g in catalog["groups"]
        }
        runs, reports = (
            RunService(lab.engine, lab.settings),
            ReportService(lab.engine, lab.settings),
        )
        plans = [
            {**p, "runs": {side: str(uuid4()) for side in p["sides"]}} for p in catalog["order"]
        ]
        write_json(output / "planned.json", plans)
        ordinal = 0
        for plan in plans:
            for side in plan["sides"]:
                disk_guard()
                if binding() != frozen:
                    raise RuntimeError("Source changed; refusing mixed-source experiment")
                ordinal += 1
                identifier = plan["runs"][side]
                directory = output / (plan["pair"] + "-" + side)
                directory.mkdir()
                runs.create(
                    1,
                    CreateRun(
                        request_id=identifier,
                        target_version_id=runtime.version_id,
                        case_revision_ids=[case_id],
                        seed=plan["seed"],
                        fault_profile_id=profiles[plan["group"]] if side == "fault" else None,
                        limits=Limits(**catalog["limits"]),
                        budgets=RoleBudgets(agent_microusd=100_000),
                    ),
                    workflow={
                        "paired_experiment": {
                            "catalog_hash": catalog["hash"],
                            "pair_id": plan["pair"],
                            "side": side,
                            "execution_order": ordinal,
                        }
                    },
                )
                execution = runs.read(1, identifier)["executions"][0]["execution_id"]
                write_json(
                    directory / "started.json", {"run_id": identifier, "execution_id": execution}
                )
                started = monotonic()
                with (
                    patch("app.executors.runner.ExecutorBudget", BenchmarkBudget)
                    if real
                    else nullcontext(),
                    patch("app.agents.gateway.remember_success", lambda *_: None),
                ):
                    result = run_execution(
                        lab.engine,
                        redis,
                        lab.settings,
                        execution,
                        **({} if real else {"gateway_factory": lambda *_: OfflineGateway()}),
                    )
                write_json(
                    directory / "runner.json",
                    {"result": result, "elapsed_seconds": monotonic() - started},
                )
                if result.get("status") == "waiting_input":
                    runs.cancel(1, identifier)
                    write_json(
                        directory / "administrative-cancel.json",
                        {"reason": "frozen_script_exhausted", "unknown_not_success": True},
                    )
                report = reports.read(1, identifier)
                write_json(directory / "report.json", report)
                exported = reports.export(1, identifier, ExportReport(request_id=uuid4()))
                write_json(directory / "exports.json", exported)
                for item in exported["items"]:
                    payload, _, _, digest = reports.download(1, item["artifact_id"])
                    suffix = {
                        "report_json": "json",
                        "report_markdown": "md",
                        "report_bundle": "zip",
                    }[item["kind"]]
                    (directory / ("export." + suffix)).write_bytes(payload)
                    write_json(directory / ("export-" + suffix + "-hash.json"), {"sha256": digest})
                records.append(
                    {
                        "pair": plan["pair"],
                        "side": side,
                        "run_id": identifier,
                        "execution_id": execution,
                        "order": ordinal,
                        "status": report["executions"][0]["status"],
                    }
                )
                write_json(output / "progress.json", records)
            compared = reports.fault_comparison(1, plan["runs"]["normal"], plan["runs"]["fault"])
            comparisons.append(compared)
            write_json(output / (plan["pair"] + "-comparison.json"), compared)
        write_json(
            output / "result.json",
            {
                "records": records,
                "comparisons": comparisons,
                "real_provider": real,
                "no_resampling": True,
            },
        )
    finally:
        write_json(
            output / "ledger-final.json",
            {k.decode(): v.decode() for k, v in redis.hgetall(LEDGER).items()},
        )
        lab.archive()
        redis.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    execute_batch(
        args.output.resolve(),
        real=args.real,
        approval=json.loads(args.approval.read_bytes()) if args.approval else None,
    )


if __name__ == "__main__":
    main()
