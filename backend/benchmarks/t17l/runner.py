"""Explicit eight-execution CLI; cannot be scheduled by the main Worker."""

import argparse
import json
import random
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.evaluation.engine import evaluator_manifest
from app.evaluation.service import load_evidence
from app.executors.runner import run_execution
from app.executors.service import RunService, booking_snapshot, case_content
from app.faults.service import FaultService
from app.models import AssertionResult, TestCase, TestExecution, TestRun, User
from app.reports.service import ReportService
from app.schemas.faults import FaultSpec
from app.schemas.reports import ExportReport
from app.schemas.runs import CreateRun, RoleBudgets

from .analysis import assess, execution_boundary, summarize
from .budget import BenchmarkBudget, initialize_ledger
from .common import AUTHORIZATION, LEDGER, catalog, disk_guard, source_fingerprint, write_json
from .lab import Lab
from .mutations import variant
from .offline import OfflineGateway


def binding():
    return {
        "schema": "t17l-harness-v1",
        "catalog_hash": canonical_hash(catalog()),
        "harness_hash": source_fingerprint(),
        "evaluator": evaluator_manifest(),
    }


def add_case(engine, group, reference):
    with Session(engine) as session, session.begin():
        reviewer = session.scalar(select(User).where(User.role == "reviewer"))
        row = TestCase(
            case_key="T17L-" + group["id"].upper(),
            revision=1,
            family_key="T17L-constructed-" + group["id"],
            category="normal",
            dataset_split="dev",
            source="mechanical_mutation_benchmark_not_independent_human_label",
            turns_json=[{"user": value} for value in group["turns"]],
            fixture_json={"fixture": "booking-default-v1", "reference_clock": reference},
            expected_json={
                "assertions": [{"type": "booking_count", "equals": group["expected_count"]}]
            },
            review_status="approved",
            reviewer_id=reviewer.id,
            reviewed_at=datetime.now(UTC),
            content_hash="0" * 64,
        )
        row.content_hash = canonical_hash(case_content(row))
        session.add(row)
        session.flush()
        return row.id


def make_blind_packet(results, output, frozen):
    order = list(results)
    random.SystemRandom().shuffle(order)
    entries, mapping = [], {}
    for index, row in enumerate(order, 1):
        identifier = f"Q{index:02}"
        entries.append(
            {
                "id": identifier,
                "user_inputs": row["user_inputs"],
                "answer": row["answer"] or "（本执行未形成最终回答）",
            }
        )
        mapping[identifier] = {
            k: row[k] for k in ("group", "side", "execution_id", "classification")
        }
    # Public packet deliberately excludes the experiment binding containing group names.
    packet = {
        "schema": "t17l-answer-only-v1",
        "items": entries,
        "instructions": (
            "仅依据输入和最终回答独立判断；未给出的SQL/工具不能推断为0。不确定选信息不足。"
        ),
    }
    write_json(output / "answer-only.json", packet)
    write_json(
        output / "private-unblinding.json",
        {"mapping": mapping, "packet_hash": canonical_hash(packet), "frozen_binding": frozen},
    )


def execute_batch(output, *, real=False, approval=None):
    frozen = binding()
    if real:
        if not approval or approval != {
            "authorization": AUTHORIZATION,
            "agent_microusd": 1_000_000,
            "max_requests": 120,
            "binding": frozen,
            "human_approved": True,
        }:
            raise RuntimeError("Explicit approval file does not match the frozen experiment")
        # Shared across all output directories: a second invocation cannot repeat paid work.
        marker = PROJECT_ROOT / ".local/t17l/real-started.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        with marker.open("x", encoding="utf-8") as handle:
            json.dump(
                {"at": datetime.now(UTC).isoformat(), "binding": frozen, "output": str(output)},
                handle,
            )
    lab = Lab(output, real=real)
    redis = Redis.from_url(lab.settings.redis_url, socket_timeout=15)
    results = []
    write_json(output / "frozen-binding.json", frozen)
    try:
        if real:
            initialize_ledger(redis)
        data = catalog()
        ids = {g["id"]: add_case(lab.engine, g, data["reference_clock"]) for g in data["groups"]}
        for entry in data["order"]:
            disk_guard()
            if binding() != frozen:
                raise RuntimeError("Catalog, harness or detector changed during batch")
            group_id, side = entry.split(":")
            group = next(g for g in data["groups"] if g["id"] == group_id)
            directory = output / entry.replace(":", "-")
            directory.mkdir()
            with variant(lab.engine, lab.settings, group_id, side, frozen) as activations:
                runtime = AgentRuntime(
                    lab.engine,
                    redis,
                    lab.settings,
                    auto_register=True,
                    prefix=lab.settings.executor_namespace + ":session",
                )
                fault = None
                if group_id == "receipt":
                    fault = FaultService(lab.engine).create(
                        FaultSpec(
                            profile_key="T17L-LOST-RESPONSE",
                            fault_type="F04",
                            trigger_stage="POST_COMMIT",
                            target_tool="confirm_booking",
                            seed=13029,
                        )
                    )["id"]
                service = RunService(lab.engine, lab.settings)
                request = CreateRun(
                    request_id=uuid4(),
                    target_version_id=runtime.version_id,
                    case_revision_ids=[ids[group_id]],
                    seed=13029,
                    fault_profile_id=fault,
                    budgets=RoleBudgets(agent_microusd=100_000 if real else 0),
                )
                run_id = service.create(1, request)
                execution_id = service.read(1, run_id)["executions"][0]["execution_id"]
                write_json(
                    directory / "started.json",
                    {
                        "run_id": run_id,
                        "execution_id": execution_id,
                        "binding": frozen,
                        "real_provider": real,
                    },
                )
                gateway = OfflineGateway(group_id)
                started = time.monotonic()
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
                        execution_id,
                        **({} if real else {"gateway_factory": lambda *_, g=gateway: g}),
                    )
                write_json(directory / "runner.json", result)
                write_json(directory / "activations.json", activations)
                if result.get("status") == "waiting_input":
                    # No extra model turn is allowed beyond the frozen script. Preserve
                    # waiting as an experiment outcome; cancel only to seal/export facts.
                    service.cancel(1, run_id)
                    write_json(
                        directory / "administrative-cancel.json",
                        {
                            "reason": "frozen_script_exhausted_while_waiting",
                            "not_a_model_retry": True,
                        },
                    )
                report = ReportService(lab.engine, lab.settings).read(1, run_id)
                write_json(directory / "report.json", report)
                exported = ReportService(lab.engine, lab.settings).export(
                    1, run_id, ExportReport(request_id=uuid4())
                )
                write_json(directory / "exports.json", exported)
                saved = report["executions"][0]
                with Session(lab.engine) as session:
                    execution = session.scalar(
                        select(TestExecution).where(TestExecution.execution_uuid == execution_id)
                    )
                    run = session.get(TestRun, execution.run_id)
                    bundle = load_evidence(session, run, execution)
                    rows = booking_snapshot(session, execution.scope_id)
                    write_json(
                        directory / "observations.json",
                        {
                            "sql": rows,
                            "events": bundle.events,
                            "output": execution.final_output_json,
                        },
                    )
                    inputs = [
                        e["payload"]["text"] for e in bundle.events if e["type"] == "input.received"
                    ]
                    answer = (execution.final_output_json or {}).get("assistant_text", "")
                    assertion_times = {
                        r.id: r.created_at
                        for r in session.scalars(
                            select(AssertionResult).where(
                                AssertionResult.execution_id == execution.id
                            )
                        )
                    }
                outcome = assess(
                    group, side, rows, activations, saved["assertions"], saved["evidence_integrity"]
                )
                outcome = execution_boundary(outcome, result, inputs, group["turns"])
                first_activation = min(
                    (datetime.fromisoformat(a["at"]) for a in activations), default=None
                )
                first_detection = min(
                    (assertion_times[i] for i in outcome["relevant_assertion_ids"]), default=None
                )
                outcome["timing"] = {
                    "execution_and_report_seconds": round(time.monotonic() - started, 3),
                    "first_mutation_observation_at": first_activation,
                    "first_related_assertion_at": first_detection,
                    "observation_to_saved_assertion_seconds": max(
                        0, (first_detection - first_activation).total_seconds()
                    )
                    if first_detection and first_activation
                    else None,
                    "boundary": "Observation to saved rule result; not human localization time.",
                }
                results.append(
                    {
                        **outcome,
                        "execution_id": execution_id,
                        "run_id": run_id,
                        "status": saved["status"],
                        "user_inputs": inputs,
                        "answer": answer,
                    }
                )
                write_json(directory / "outcome.json", results[-1])
                write_json(output / "progress.json", {"completed": results, "planned": 8})
                print(
                    json.dumps(
                        {
                            "entry": entry,
                            "status": saved["status"],
                            "classification": outcome["classification"],
                            "sql_count": outcome["sql_count"],
                        }
                    ),
                    flush=True,
                )
        make_blind_packet(results, output, frozen)
        write_json(
            output / "summary.json",
            {
                "mode": "real_provider" if real else "offline_test_double",
                "binding": frozen,
                "results": results,
                "summary": summarize(results),
                "ledger": {k.decode(): int(v) for k, v in redis.hgetall(LEDGER).items()}
                if real
                else None,
                "human_comparison": "pending_independent_labels",
            },
        )
        lab.archive()
        return results
    finally:
        # Keep all uncertain databases/evidence on exception. Never reset a budget or
        # delete checkpoints shared with other experiments. No automatic model retries.
        lab.engine.dispose()
        lab.admin.dispose()
        redis.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / ".local/t17l").resolve()):
        parser.error("Output must stay under the project-local T17L directory")
    approval = json.loads(args.approval.read_text(encoding="utf-8")) if args.approval else None
    execute_batch(output, real=args.real, approval=approval)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
