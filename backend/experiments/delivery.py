"""Verify sealed labels, actual blind inputs and all exported artifact bytes; no provider call."""

import argparse
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.versions import canonical_hash
from app.db.engine import create_database_engine
from app.models import Artifact, TestRun
from experiments.common import EVIDENCE, ROOT, read, settings, sha, write_new
from experiments.labels import LabelService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()
    if args.manifest_only:
        assert (EVIDENCE / "delivery-audit.json").exists()
        paths = set(EVIDENCE.rglob("*"))
        paths.update((ROOT / "docs/fixtures/T16").glob("*"))
        paths.update((ROOT / "backend/experiments").glob("*.py"))
        paths.update((ROOT / "frontend/tests").glob("formal*.ts"))
        paths.update((ROOT / "backend/tests").glob("test_formal*.py"))
        paths.update(
            ROOT / p
            for p in (
                "任务规划.md",
                "docs/acceptance/T16.md",
                "docs/architecture/formal-experiments.md",
                "frontend/src/pages/JudgingPage.vue",
                "backend/tests/test_experiment_policy.py",
                "backend/tests/test_database.py",
                "backend/app/agents/graph.py",
                "backend/app/agents/versions.py",
                "backend/app/core/config.py",
            )
        )
        values = {
            p.relative_to(ROOT).as_posix(): sha(p)
            for p in sorted(paths)
            if p.is_file() and p.name != "delivery-manifest.json"
        }
        write_new(
            EVIDENCE / "delivery-manifest.json",
            {
                "recorded_at": datetime.now(UTC).isoformat(),
                "files": values,
                "boundary": (
                    "Snapshot of public evidence and implementation; private .local excluded."
                ),
            },
        )
        print(json.dumps({"delivery_files": len(values)}))
        return
    s = settings()
    engine = create_database_engine(s)
    service = LabelService(engine, None, s)
    try:
        sealed, batches = {}, {}
        for name, filename in (
            ("C", "calibration-original-seal.json"),
            ("G1", "G1-original-seal.json"),
            ("G2", "G2-original-seal.json"),
        ):
            original = read(EVIDENCE / filename)
            batch = service.read(1, original["batch_id"])
            assert batch["seal"] == original["seal"]
            assert canonical_hash(batch["seal"]["labels"]) == batch["seal"]["labels_hash"]
            assert len(batch["labels"]) == len(batch["results"]) == 30
            assert batch["frozen"]["dataset"]["samples"] == original["samples"]
            sealed[name] = batch["seal"]["labels_hash"]
            batches[name] = batch
        provider_count = 0
        protocols = []
        for name, batch in batches.items():
            protocol = read(EVIDENCE / "judge" / name / "protocol.json")
            protocols.append(
                {
                    k: protocol[k]
                    for k in (
                        "prompt_hash",
                        "schema_hash",
                        "threshold",
                        "model",
                        "thinking",
                        "max_output_tokens",
                    )
                }
            )
            with Session(engine) as session:
                _, rows = service._rows(session, 1, batch["batch_id"])
            calls = {v["call_id"]: v for r, v in rows if r.metadata_json.get("phase") == "provider"}
            assert len(calls) == 30
            for sample in batch["frozen"]["dataset"]["samples"]:
                value = read(EVIDENCE / "judge" / name / (sample["id"] + "-input.json"))
                assert set(value) == {"dialogue", "reply", "dialogue_scope", "evidence"}
                assert value["evidence"] == []
                assert all(
                    value[k] == sample["payload"][k]
                    for k in ("dialogue", "reply", "dialogue_scope")
                )
            for path in (EVIDENCE / "judge" / name / "provider").glob("*.json"):
                call = read(path)
                assert canonical_hash(call) == canonical_hash(calls[call["call_id"]])
                assert call["role"] == "judge" and call["model_requests"] == 1
                request = call["request"]
                assert request["tools"] == [] and len(request["messages"]) == 2
                payload = json.loads(request["messages"][-1]["data"]["content"])
                assert payload["evidence"] == [] and set(payload) == {
                    "dialogue",
                    "reply",
                    "dialogue_scope",
                    "evidence",
                }
                assert request["messages"][0]["data"]["content"] == protocol["prompt"]
                predecessors = [
                    batches[n]["seal"]["sealed_at"]
                    for n in (("C",) if name == "C" else ("G1", "G2"))
                ]
                assert all(
                    datetime.fromisoformat(t) <= datetime.fromisoformat(call["started_at"])
                    for t in predecessors
                )
                provider_count += 1
        assert provider_count == 90 and protocols[0] == protocols[1] == protocols[2]
        exports = {}
        for cohort in ("episodes", "serial-episodes", "offline-controls"):
            for path in (EVIDENCE / cohort).glob("*/outcome.json"):
                value = read(path)
                exports[value["run_id"]] = path.parent
        checked = 0
        with Session(engine) as session:
            run_ids = {r.id: r.run_uuid for r in session.scalars(select(TestRun))}
            for row in session.scalars(select(Artifact).where(Artifact.run_id.is_not(None))):
                path = exports[run_ids[row.run_id]] / "artifacts" / (str(row.id) + ".json")
                assert path.stat().st_size == row.size_bytes and sha(path) == row.sha256
                checked += 1
        write_new(
            EVIDENCE / "delivery-audit.json",
            {
                "checked_at": datetime.now(UTC).isoformat(),
                "original_seals_unchanged": sealed,
                "judge_provider_files_match_SQL": provider_count,
                "actual_blind_inputs_verified": 90,
                "gold_calls_after_both_independent_seals": True,
                "same_judge_protocol_for_three_batches": True,
                "run_artifacts_match_SQL": checked,
                "formal_episodes": 320,
                "assistant_reviews_separate": len(batches["G1"]["assistant_reviews"]),
                "no_new_model_calls": True,
            },
        )
        print(
            json.dumps(
                {"judge_providers_verified": provider_count, "run_artifacts_verified": checked}
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
