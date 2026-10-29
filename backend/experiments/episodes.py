"""Cold binding/requests and isolated execution from either frozen application tree."""

import argparse
import inspect
import json
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from types import FunctionType
from uuid import UUID, uuid5

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.versions import canonical_hash, register_version
from app.datasets.catalog import experiment_plan, load_catalog
from app.evaluation.service import load_evidence
from app.evidence.reader import TraceReader
from app.executors import runner
from app.executors.service import RunService, frozen_case
from app.models import Artifact, TestCase, TestExecution
from app.schemas.runs import CreateRun
from experiments.budget import FormalExecutorBudget
from experiments.common import EVIDENCE, LOCAL, ROOT, read, settings, sha, write_new

NAMESPACE = UUID("dc82aaf5-5ae4-4494-b97a-0810c1f23a4d")


def bind(engine, s, version):
    frozen = read(ROOT / "datasets/v1.1/frozen-manifest.json")
    old = read(ROOT / "docs/evidence/T13/version-bindings.json")
    from app.models import TargetVersion

    result = {}
    with Session(engine) as session:
        for name, entry in old["V0"].items():
            original = session.get(TargetVersion, entry["target_version_id"])
            ids = [d["id"] for d in original.model_config_json["retrieval"]["documents"]]
            manifest = RunService(engine, s, document_ids=ids).current_manifest()
            if version == "V0":
                assert manifest["config_hash"] == entry["config_hash"], name
            else:
                assert (
                    manifest["baseline"] == "V1"
                    and manifest["execution_policy"] == "single_call_v1"
                )
                for field in (
                    "model_requested",
                    "thinking",
                    "prompt_hash",
                    "retrieval",
                    "tools",
                    "context",
                    "sdk_versions",
                ):
                    assert manifest[field] == original.model_config_json[field], (name, field)
            result[name] = {
                "target_version_id": register_version(engine, manifest),
                "config_hash": manifest["config_hash"],
                "document_ids": ids,
            }
    assert frozen["package_hash"] == load_catalog()["package_hash"]
    return result


def request_for(engine, s, job, bindings):
    frozen = read(ROOT / "datasets/v1.1/frozen-manifest.json")
    case = next(c for c in load_catalog()["cases"] if c["id"] == job["case_key"])
    with Session(engine) as session:
        row = session.get(TestCase, frozen["case_revisions"][case["id"]]["id"])
        frozen_case(row)
        assert row.content_hash == frozen["case_revisions"][case["id"]]["content_hash"]
    old = read(ROOT / "docs/evidence/T13/version-bindings.json")
    return CreateRun(
        request_id=uuid5(
            NAMESPACE,
            frozen["package_hash"]
            + ":"
            + job["version"]
            + ":"
            + case["id"]
            + ":"
            + str(job["repeat_no"]),
        ),
        target_version_id=bindings[case["knowledge_profile"]]["target_version_id"],
        case_revision_ids=[row.id],
        repetitions=1,
        seed=job["seed"],
        limits=case["limits"],
        budgets={"agent_microusd": job["agent_cap_microusd"]},
        fault_profile_id=old["fault_profiles"][canonical_hash(case["fault"])]
        if case["fault"]
        else None,
    )


def export(engine, s, identifier, dest):
    reader = TraceReader(engine, s)
    snapshot = reader.snapshot(1, identifier)
    write_new(dest / "run.json", snapshot)
    cursor, events = None, []
    while True:
        page = reader.page(1, identifier, after=cursor, limit=500)
        events.extend(page["items"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert len(events) == snapshot["event_count"]
    assert [e["event"]["run_seq"] for e in events] == list(range(1, len(events) + 1))
    write_new(dest / "events.json", events)
    with Session(engine) as session:
        run = RunService.owned_run(session, 1, identifier)
        execution = session.scalar(select(TestExecution).where(TestExecution.run_id == run.id))
        bundle = load_evidence(session, run, execution)
        write_new(dest / "observations.json", asdict(bundle))
        for row in session.scalars(
            select(Artifact).where(
                Artifact.run_id == RunService.owned_run(session, 1, identifier).id
            )
        ):
            from app.core.config import PROJECT_ROOT

            path = PROJECT_ROOT / row.relative_path
            assert path.stat().st_size == row.size_bytes and sha(path) == row.sha256
            target = dest / "artifacts" / (str(row.id) + ".json")
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, target)
    from app.core.config import PROJECT_ROOT

    provider = PROJECT_ROOT / ".local/t05/model-calls" / identifier
    records = []
    for path in sorted(provider.rglob("*.json")):
        data = path.read_bytes()
        assert s.deepseek_api_key.get_secret_value().encode() not in data
        target = dest / "provider" / path.name
        target.parent.mkdir(exist_ok=True)
        with target.open("xb") as file:
            file.write(data)
        records.append({"path": "provider/" + path.name, "sha256": sha(path)})
    write_new(dest / "provider-manifest.json", records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=["V0", "V1"], required=True)
    parser.add_argument("--case")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--offline-control", action="store_true")
    parser.add_argument("--cohort", choices=["pilot", "serial"], default="serial")
    args = parser.parse_args()
    s = settings(version=args.version, paid=args.execute and not args.offline_control)
    from app.db.engine import create_database_engine

    engine = create_database_engine(s)
    try:
        output = EVIDENCE / (args.version + "-bindings.json")
        bindings = read(output) if args.case and output.exists() else bind(engine, s, args.version)
        if output.exists():
            assert read(output) == bindings
        else:
            write_new(output, bindings)
        if not args.case:
            print(json.dumps({"profiles": 12, "version": args.version, "paid_calls": 0}))
            return
        job = next(
            j
            for j in experiment_plan(load_catalog())["E2"]["episodes"]
            if j["version"] == args.version
            and j["case_key"] == args.case
            and j["repeat_no"] == args.repeat
        )
        request = request_for(engine, s, job, bindings)
        if args.cohort == "serial":
            request = request.model_copy(
                update={
                    "request_id": uuid5(NAMESPACE, str(request.request_id) + ":serial-formal-v1")
                }
            )
        if args.offline_control:
            request = request.model_copy(
                update={
                    "request_id": uuid5(NAMESPACE, str(request.request_id) + ":offline-control")
                }
            )
        directory = (
            "offline-controls"
            if args.offline_control
            else ("serial-episodes" if args.cohort == "serial" else "episodes")
        )
        dest = EVIDENCE / directory / f"{args.version}-{args.case}-{args.repeat}"
        if not args.execute:
            print(json.dumps({"request": request.model_dump(mode="json"), "paid_calls": 0}))
            return
        if (dest / "outcome.json").exists():
            print(json.dumps({"already_recorded": True, "outcome": str(dest)}))
            return
        dest.mkdir(parents=True, exist_ok=False)
        assert shutil.disk_usage("C:/").free > 1_500_000_000
        assert not (LOCAL / "stop").exists(), "T16 stop marker exists"
        # Same frozen function bytecode and dependencies, except the separately audited budget.
        # No gateway_factory: real-provider mode stays correctly recorded by the original runner.
        run = FunctionType(
            runner.run_execution.__code__,
            {
                **runner.run_execution.__globals__,
                "ExecutorBudget": FormalExecutorBudget,
            },
            runner.run_execution.__name__,
            runner.run_execution.__defaults__,
            runner.run_execution.__closure__,
        )
        run.__kwdefaults__ = dict(runner.run_execution.__kwdefaults__ or {})
        write_new(
            dest / "plan.json",
            {
                "episode": job,
                "cohort": args.cohort,
                "global_concurrency": 1 if args.cohort == "serial" else 2,
                "request": request.model_dump(mode="json"),
                "harness": {p.name: sha(p) for p in (ROOT / "backend/experiments").glob("*.py")},
                "runner_function_sha256": __import__("hashlib")
                .sha256(inspect.getsource(runner.run_execution).encode())
                .hexdigest(),
                "budget_adapter": (
                    "FormalExecutorBudget; reserve caps/authorization only; "
                    "inherited settle/unknown and SQL ticket mirrors"
                ),
                "planned_repeat_is_not_infrastructure_attempt": True,
            },
        )
        service = RunService(
            engine, s, document_ids=bindings[job["knowledge_profile"]]["document_ids"]
        )
        identifier = service.create(1, request)
        with Session(engine) as session:
            execution = session.scalar(
                select(TestExecution).where(
                    TestExecution.run_id == RunService.owned_run(session, 1, identifier).id
                )
            )
            execution_uuid = execution.execution_uuid
        redis = Redis.from_url(s.redis_url, socket_timeout=15)
        if args.offline_control:
            from langchain_core.messages import AIMessage

            class OfflineControl:
                def invoke(self, *args, **kwargs):
                    return AIMessage(
                        content="明确离线控制：没有发送模型请求，也没有执行预约工具。"
                    ), {
                        "call_id": "explicit-offline-control",
                        "model_requests": 0,
                        "response": {"model_returned": "offline-control"},
                    }

            result = run(
                engine, redis, s, execution_uuid, gateway_factory=lambda *_: OfflineControl()
            )
        else:
            result = run(engine, redis, s, execution_uuid)
        export(engine, s, identifier, dest)
        write_new(
            dest / "outcome.json",
            {
                "run_id": identifier,
                "execution_id": execution_uuid,
                "runner": result,
                "episode": job,
                "offline_control": args.offline_control,
                "cohort": args.cohort,
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        redis.close()
        print(json.dumps({"episode": job, "result": result, "exported": str(dest)}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
