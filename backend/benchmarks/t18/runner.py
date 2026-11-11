"""Eight declared cases; no retries/resampling and no real payment side effects."""

import argparse
import json
import sqlite3
from contextlib import closing, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from redis import Redis
from sqlalchemy.orm import Session

from app.adapters.refund import RefundAdapter
from app.adapters.refund_catalog import CASES, register
from app.core.config import PROJECT_ROOT
from app.evaluation.service import load_evidence
from app.executors.runner import run_execution
from app.executors.service import RunService
from app.faults.service import FaultService
from app.models import TestExecution, TestRun
from app.reports.service import ReportService
from app.schemas.faults import FaultSpec
from app.schemas.reports import ExportReport
from app.schemas.runs import CreateRun, RoleBudgets
from benchmarks.t17l.common import disk_guard, write_json
from benchmarks.t17l.lab import Lab
from reference_agents.refund.store import RefundStore

from .budget import BenchmarkBudget, initialize_ledger
from .common import AUTHORIZATION, LEDGER, binding
from .offline import OfflineGateway


def required_approval():
    return {
        "authorization": AUTHORIZATION,
        "agent_microusd": 1_500_000,
        "max_requests": 160,
        "max_executions": 8,
        "binding": binding(),
        "human_approved": True,
    }


def execute_batch(output, *, real=False, approval=None):
    frozen = binding()
    if real:
        if approval != required_approval():
            raise RuntimeError("T18 approval binding mismatch")
        disk_guard()
        with (PROJECT_ROOT / ".local/t18/real-started.json").open("x", encoding="utf-8") as f:
            json.dump({"binding": frozen, "at": datetime.now(UTC).isoformat()}, f)
    lab = Lab(output, real=real)
    lab.settings = lab.settings.model_copy(
        update={"executor_budget_authorization_id": AUTHORIZATION, "executor_agent_budget_usd": 1.5}
    )
    redis = Redis.from_url(lab.settings.redis_url, socket_timeout=15)
    records = []
    try:
        if real:
            initialize_ledger(redis)
        target = register(lab.engine, lab.settings)
        write_json(output / "target.json", target)
        write_json(output / "approval.json", approval if real else {"offline_test_double": True})
        write_json(output / "binding.json", frozen)
        planned = [str(uuid4()) for _ in CASES]
        write_json(output / "planned.json", planned)
        runs, reports = (
            RunService(lab.engine, lab.settings),
            ReportService(lab.engine, lab.settings),
        )
        for index, (case, identifier) in enumerate(zip(CASES, planned, strict=True)):
            disk_guard()
            if binding() != frozen:
                raise RuntimeError("Source changed during frozen batch")
            key, title, turns, count, total, order, fault = case
            profile = (
                FaultService(lab.engine).create(
                    FaultSpec(
                        profile_key="T18-" + fault,
                        fault_type=fault,
                        trigger_stage="POST_COMMIT" if fault == "F04" else "PRE_DISPATCH",
                        target_tool="confirm_refund",
                        seed=18001,
                    )
                )["id"]
                if fault
                else None
            )
            directory = output / key.lower()
            directory.mkdir()
            runs.create(
                1,
                CreateRun(
                    request_id=identifier,
                    target_version_id=target["version_id"],
                    case_revision_ids=[target["case_ids"][index]],
                    seed=18002,
                    budgets=RoleBudgets(agent_microusd=100_000),
                    fault_profile_id=profile,
                ),
            )
            execution = runs.read(1, identifier)["executions"][0]["execution_id"]
            write_json(
                directory / "started.json", {"run_id": identifier, "execution_id": execution}
            )
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
            write_json(directory / "runner.json", result)
            if result.get("status") == "waiting_input":
                runs.cancel(1, identifier)
                write_json(
                    directory / "administrative-cancel.json",
                    {"reason": "frozen_script_exhausted", "unknown_not_success": True},
                )
            report = reports.read(1, identifier)
            write_json(directory / "report.json", report)
            with Session(lab.engine) as session:
                run = (
                    session.get(TestRun, report["run"]["database_id"])
                    if "database_id" in report["run"]
                    else RunService.owned_run(session, 1, identifier)
                )
                row = session.get(TestExecution, report["executions"][0]["database_execution_id"])
                evidence = load_evidence(session, run, row, lock_events=False)
                write_json(directory / "events.json", evidence.events)
                store = RefundAdapter(lab.engine, lab.settings).store(row.scope_id, 1)
                if store.path.exists():
                    with (
                        closing(store.connect()) as source,
                        closing(sqlite3.connect(directory / "ledger.sqlite3")) as dest,
                    ):
                        source.backup(dest)
                        assert dest.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                        assert list(source.iterdump()) == list(dest.iterdump())
                    assert RefundStore(directory / "ledger.sqlite3", 1).snapshot() == evidence.after
            exported = reports.export(1, identifier, ExportReport(request_id=uuid4()))
            write_json(directory / "exports.json", exported)
            for item in exported["items"]:
                payload, _, _, _ = reports.download(1, item["artifact_id"])
                suffix = {"report_json": "json", "report_markdown": "md", "report_bundle": "zip"}[
                    item["kind"]
                ]
                (directory / ("export." + suffix)).write_bytes(payload)
            records.append(
                {
                    "case": key,
                    "title": title,
                    "run_id": identifier,
                    "execution_id": execution,
                    "status": report["executions"][0]["status"],
                    "rule_verdict": report["statistics"]["rule_outcomes"],
                    "sql": report["executions"][0]["after"],
                    "fault": fault,
                    "evidence_complete": evidence.complete,
                }
            )
            write_json(output / "progress.json", records)
        write_json(
            output / "result.json",
            {
                "records": records,
                "real_provider": real,
                "provenance": target["provenance"],
                "no_resampling": True,
                "planned": 8,
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
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute_batch(
        args.output.resolve(),
        real=args.real,
        approval=json.loads(args.approval.read_bytes()) if args.approval else None,
    )


if __name__ == "__main__":
    main()
