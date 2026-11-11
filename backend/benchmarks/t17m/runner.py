"""Three predeclared original trials, then at most two reviewed candidates × three."""

import argparse
import json
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
from app.executors.runner import run_execution
from app.executors.service import RunService, case_content
from app.models import TestCase, User
from app.reductions.engine import signature
from app.reductions.service import ReductionService
from app.reports.service import ReportService
from app.schemas.reductions import (
    ReductionCreate,
    ReductionExecute,
    ReductionReview,
    ReductionStage,
)
from app.schemas.reports import ExportReport
from app.schemas.runs import CreateRun, RoleBudgets
from benchmarks.t17l.common import disk_guard, write_json
from benchmarks.t17l.lab import Lab
from benchmarks.t17l.mutations import variant

from .budget import BenchmarkBudget, initialize_ledger
from .common import AUTHORIZATION, LEDGER, PROPOSAL, binding


def execute_batch(output, approval):
    frozen = binding()
    semantic = json.loads((PROJECT_ROOT / ".local/t17m/semantic-approval.json").read_bytes())
    proposal = json.loads(PROPOSAL.read_bytes())
    body = {k: v for k, v in proposal.items() if k != "hash"}
    if (
        proposal["hash"] != canonical_hash(body)
        or semantic.get("proposal_hash") != proposal["hash"]
        or not semantic.get("human_approved")
    ):
        raise RuntimeError("Human semantic approval must match the frozen inputs")
    expected = {
        "authorization": AUTHORIZATION,
        "agent_microusd": 1_000_000,
        "max_requests": 180,
        "binding": frozen,
        "semantic_hash": proposal["hash"],
        "human_approved": True,
    }
    if approval != expected:
        raise RuntimeError("Budget approval does not match the exact experiment binding")
    disk_guard()
    marker = PROJECT_ROOT / ".local/t17m/real-started.json"
    with marker.open("x", encoding="utf-8") as handle:
        json.dump(
            {"at": datetime.now(UTC).isoformat(), "output": str(output), "binding": frozen}, handle
        )
    lab = Lab(output, real=True)
    lab.settings = lab.settings.model_copy(
        update={"executor_budget_authorization_id": AUTHORIZATION}
    )
    redis = Redis.from_url(lab.settings.redis_url, socket_timeout=15)
    initialize_ledger(redis)
    all_runs, records = [], []
    write_json(output / "approval.json", approval)

    def execute(identifier, label, activations):
        disk_guard()
        if binding() != frozen:
            raise RuntimeError("Frozen source changed; stop instead of mixing versions")
        directory = output / label
        directory.mkdir()
        runs = RunService(lab.engine, lab.settings)
        execution = runs.read(1, identifier)["executions"][0]["execution_id"]
        write_json(directory / "started.json", {"run_id": identifier, "execution_id": execution})
        before = len(activations)
        with (
            patch("app.executors.runner.ExecutorBudget", BenchmarkBudget),
            patch("app.agents.gateway.remember_success", lambda *_: None),
        ):
            result = run_execution(lab.engine, redis, lab.settings, execution)
        write_json(directory / "runner.json", result)
        if result.get("status") == "waiting_input":
            runs.cancel(1, identifier)
            write_json(
                directory / "administrative-cancel.json",
                {"reason": "frozen_script_exhausted", "not_reproduced": False},
            )
        reports = ReportService(lab.engine, lab.settings)
        report = reports.read(1, identifier)
        write_json(directory / "report.json", report)
        write_json(directory / "activations.json", activations[before:])
        write_json(
            directory / "exports.json",
            reports.export(1, identifier, ExportReport(request_id=uuid4())),
        )
        records.append(
            {
                "label": label,
                "run_id": identifier,
                "execution_id": execution,
                "status": report["executions"][0]["status"],
                "same_signature": signature(report["executions"][0]),
                "activations": len(activations) - before,
            }
        )
        return report

    try:
        with variant(
            lab.engine,
            lab.settings,
            "confirmation",
            "mutant",
            {"experiment": "T17M", "binding_hash": canonical_hash(frozen)},
        ) as activations:
            runtime = AgentRuntime(
                lab.engine,
                redis,
                lab.settings,
                auto_register=True,
                prefix=lab.settings.executor_namespace + ":session",
            )
            write_json(
                output / "target.json",
                {
                    "id": runtime.version_id,
                    "manifest": runtime.manifest,
                    "boundary": "New T17M source and synthetic variant, not historical T17L.",
                },
            )
            with Session(lab.engine) as session, session.begin():
                reviewer = session.scalar(select(User.id).where(User.role == "reviewer"))
                case = TestCase(
                    case_key="T17M-ORIGINAL",
                    revision=1,
                    family_key="T17M-development-confirmation",
                    category="security",
                    dataset_split="dev",
                    source="T17M-human-confirmed-experiment-input",
                    turns_json=proposal["turns"],
                    fixture_json={
                        "fixture": "booking-default-v1",
                        "reference_clock": proposal["reference_clock"],
                    },
                    expected_json={"assertions": [proposal["expected"]]},
                    review_status="approved",
                    reviewer_id=reviewer,
                    reviewed_at=datetime.now(UTC),
                    content_hash="0" * 64,
                )
                # Human-confirmed experiment material, not independent blind labeling.
                case.content_hash = canonical_hash(case_content(case))
                session.add(case)
                session.flush()
                case_id = case.id
            runs = RunService(lab.engine, lab.settings)
            all_runs = [str(uuid4()) for _ in range(3)]
            write_json(output / "planned-originals.json", all_runs)
            originals = []
            for repeat, identifier in enumerate(all_runs):
                runs.create(
                    1,
                    CreateRun(
                        request_id=identifier,
                        target_version_id=runtime.version_id,
                        case_revision_ids=[case_id],
                        seed=13029,
                        budgets=RoleBudgets(agent_microusd=100_000),
                    ),
                )
                originals.append(execute(identifier, f"original-{repeat + 1}", activations))
            failed = next((r for r in originals if signature(r["executions"][0])), None)
            if failed:
                reports = ReportService(lab.engine, lab.settings)
                defects = reports.collect(1, failed["run"]["run_id"])["items"]
                key = next(d["defect_key"] for d in defects if d["category"] == "rule_violation")
                service = ReductionService(lab.engine, lab.settings)
                plan = service.create(
                    1, key, ReductionCreate(request_id=uuid4()), baseline_runs=all_runs
                )
                write_json(output / "plan-initial.json", plan)
                if plan["variants"][0]["verified"]:
                    for index, candidate in enumerate(proposal["candidates"], 1):
                        plan = service.review(
                            1,
                            plan["plan_id"],
                            index,
                            ReductionReview(
                                request_id=uuid4(),
                                expected_hash=plan["revision_hash"],
                                keep=candidate["keep"],
                                semantic_preserved=True,
                                human_attestation=True,
                                note="用户在当前会话明确确认保留原意；非独立盲标。proposal="
                                + proposal["hash"],
                            ),
                        )
                        plan = service.execute(
                            1,
                            plan["plan_id"],
                            index,
                            ReductionExecute(
                                request_id=uuid4(),
                                expected_hash=plan["revision_hash"],
                                paid_attestation=True,
                            ),
                        )
                        for repeat, identifier in enumerate(plan["variants"][index]["runs"]):
                            all_runs.append(identifier)
                            execute(identifier, f"candidate-{index}-{repeat + 1}", activations)
                        plan = service.read(1, plan["plan_id"])
                        write_json(output / f"plan-after-{index}.json", plan)
                    choices = [
                        i
                        for i in range(1, len(plan["variants"]))
                        if plan["variants"][i]["verified"]
                    ]
                    if choices:
                        index = min(
                            choices,
                            key=lambda i: tuple(
                                plan["variants"][i]["size"][k] for k in ("turns", "utf8_bytes")
                            ),
                        )
                        plan = service.stage(
                            1,
                            plan["plan_id"],
                            index,
                            ReductionStage(request_id=uuid4(), expected_hash=plan["revision_hash"]),
                        )
                write_json(output / "plan-final.json", plan)
            write_json(
                output / "result.json",
                {
                    "records": records,
                    "runs": all_runs,
                    "ledger": {k.decode(): v.decode() for k, v in redis.hgetall(LEDGER).items()},
                    "plan_available": bool(failed),
                    "no_valid_reduction_is_legitimate": True,
                    "sampling": "Provider determinism unasserted; fixed clock and script.",
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
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute_batch(args.output.resolve(), json.loads(args.approval.read_bytes()))


if __name__ == "__main__":
    main()
