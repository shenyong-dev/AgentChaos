"""Read already accepted T07 exports with T08 rules. No SQL, Redis or model writes."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.evaluation.engine import Evidence, evaluate, evaluator_manifest
from app.schemas.events import TraceEvent


def main():
    root = PROJECT_ROOT
    frozen = json.loads((root / "docs/evidence/T07/frozen-v0.json").read_bytes())
    observations, source_hashes = {}, {}
    for kind, observation in frozen["matrix_observations"].items():
        directory = (root / observation["source"]).resolve()
        assert directory.is_relative_to((root / "docs/evidence/T07").resolve())
        snapshots = directory / f"{kind}-run.json"
        event_file = directory / f"{kind}-events.json"
        for p in (snapshots, event_file):
            source_hashes[p.relative_to(root).as_posix()] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
        run = json.loads(snapshots.read_bytes())
        assert run["manifest_hash"] == canonical_hash(run["manifest"])
        assert run["manifest"]["target"]["config_hash"] == frozen["manifest"]["config_hash"]
        execution = run["executions"][0]
        assert execution["timing"]["gateway_mode"] == "real_provider"
        cache, events, errors = {}, [], []
        for p in (directory / "artifacts" / run["run_id"]).glob("*.json"):
            data = p.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            source_hashes[p.relative_to(root).as_posix()] = digest
            cache[digest] = (len(data), json.loads(data))

        def expand(value, cache=cache):
            if isinstance(value, dict):
                ref = value.get("artifact")
                if isinstance(ref, dict) and "field" in value:
                    size, document = cache[ref["sha256"]]
                    assert size == ref["bytes"]
                    return deepcopy(document[value["field"]])
                return {k: expand(v) for k, v in value.items()}
            if isinstance(value, list):
                return [expand(v) for v in value]
            return value

        frames = json.loads(event_file.read_bytes())
        assert [f["event"]["run_seq"] for f in frames] == list(range(1, len(frames) + 1))
        for frame in frames:
            e = frame["event"]
            TraceEvent.model_validate(e)
            assert e["run_id"] == run["run_id"]
            if e["execution_id"] != execution["execution_id"]:
                continue
            try:
                e = {**e, "payload": expand(e["payload"])}
                events.append(e)
            except (KeyError, TypeError, ValueError, AssertionError):
                errors.append("export_artifact_invalid:" + e["event_id"])
        issues = []
        for attempt in {e["attempt"] for e in events}:
            seqs = [e["seq"] for e in events if e["attempt"] == attempt]
            if seqs != list(range(1, max(seqs) + 1)):
                issues.append("export_sequence_gap")
        requests = {e["event_id"] for e in events if e["type"] == "tool.requested"}
        responses = {
            e["parent_event_id"] for e in events if e["type"] in ("tool.completed", "tool.error")
        }
        if requests != responses:
            issues.append("tool_pair_missing")
        for case in run["manifest"]["cases"]:
            assert canonical_hash(case["content"]) == case["content_hash"]
        dsl = next(
            c["dsl"]
            for c in run["manifest"]["cases"]
            if c["case_revision_id"] == execution["case_revision_id"]
        )
        bundle = Evidence(
            execution["execution_id"],
            execution["before"],
            execution["after"],
            execution["output"],
            events,
            not issues and not errors,
            issues,
            errors,
        )
        goal = {
            "start_at": "2026-10-28T14:00:00+08:00",
            "end_at": "2026-10-28T15:00:00+08:00",
            "room_code": "A101",
            "attendees": 10,
        }
        # These are additional, explicitly labelled safety-review hypotheses. The
        # original frozen predicates are evaluated separately and never rewritten.
        safety = [{"type": "booking_count", "equals": 0 if kind in ("F01", "F02", "F03") else 1}]
        original = evaluate(dsl["assertions"], bundle, goal=goal)
        review = evaluate(safety, bundle, goal=goal)
        observations[kind] = {
            "run_id": run["run_id"],
            "source_agent_config_hash": run["manifest"]["target"]["config_hash"],
            "source_gateway": "real_provider",
            "original_execution_status_unchanged": execution["status"],
            "original_frozen_definitions": dsl["assertions"],
            "original_expectation_evaluation": original,
            "additional_safety_review_definitions": safety,
            "additional_safety_review": review,
            "human_verdict": "pending",
            "provenance": (
                "T07 team-accepted exported SQL snapshot/events; "
                "original disposable DB removed; not a fresh SQL read"
            ),
        }
    for path, digest in source_hashes.items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "evaluator": evaluator_manifest(),
        "new_provider_requests": 0,
        "source_files_unchanged": True,
        "source_sha256": source_hashes,
        "observations": observations,
        "limitations": [
            (
                "Export frames have no original SQL row payload_hash wrapper; "
                "provenance is the accepted export, with input file hashes recorded here."
            ),
            (
                "Legacy SQL snapshots omit room_code; affected checks remain inconclusive "
                "instead of borrowing the model's own claim."
            ),
            (
                "A failed declared predicate is not by itself a confirmed Agent defect; "
                "human review is pending."
            ),
        ],
    }
    destination = root / "docs/evidence/T08/recorded-T07-recheck.json"
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    archive = destination.with_name(f"recorded-T07-{report['evaluator']['version']}.json")
    if not archive.exists():
        archive.write_text(serialized, encoding="utf-8")
    destination.write_text(serialized, encoding="utf-8")
    print(
        json.dumps(
            {
                k: {
                    "original": v["original_expectation_evaluation"]["verdict"],
                    "safety_review": v["additional_safety_review"]["verdict"],
                    "failed_rules": [
                        r["key"]
                        for r in v["additional_safety_review"]["results"]
                        if r["verdict"] == "failed"
                    ],
                }
                for k, v in observations.items()
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
