"""Read-only traceable E2 tables. Preserve diagnostic cohort and every missing episode."""

import csv
import json
from datetime import UTC, datetime

from app.agents.versions import canonical_hash
from app.datasets.catalog import experiment_plan, load_catalog
from experiments.analysis import episode_key, summarize
from experiments.common import EVIDENCE, ROOT, read, sha, write_new


def observation(directory):
    outcome = read(directory / "outcome.json")
    run = read(directory / "run.json")
    observed = read(directory / "observations.json")
    assert run["manifest_hash"] == canonical_hash(run["manifest"])
    assert observed["execution_id"] == outcome["execution_id"]
    execution = run["executions"][0]
    mode = execution["timing"].get("gateway_mode")
    if mode != "real_provider":
        assert outcome["runner"] == {
            "claimed": False,
            "status": "queued",
            "code": "claim_deadlock_1213",
        }
        assert outcome.get("no_retry_or_provider_call") and not execution["usage"].get("tickets")
    tickets = list(execution["usage"].get("tickets", {}).values())
    known = [t for t in tickets if t["state"] == "settled"]
    claimed_at = execution["timing"].get("claimed_at")
    elapsed = (
        (
            datetime.fromisoformat(outcome["finished_at"]) - datetime.fromisoformat(claimed_at)
        ).total_seconds()
        if claimed_at
        else None
    )
    return {
        **outcome,
        "status": execution["status"],
        "gateway_mode": mode or "not_invoked_claim_failed",
        "attempts": execution["attempt"],
        "fault_triggered": any(e["type"] == "fault.injected" for e in observed["events"]),
        "single_call_policy_rejections": sum(
            e["type"] == "tool.error" and e["payload"].get("code") == "single_tool_call_required"
            for e in observed["events"]
        ),
        "known_tokens": sum(
            t["usage"]["input_tokens"] + t["usage"]["output_tokens"] for t in known
        ),
        "unknown_usage_calls": len(tickets) - len(known),
        "known_peak_estimate_microusd": sum(t["estimate_microusd"] for t in known),
        "model_calls_started": len(tickets),
        "observation_elapsed_seconds": elapsed,
        "elapsed_boundary": (
            "Claim to export observation, including runner and SQL; not model-only latency."
        ),
        "task_outcome": execution["task_outcome"],
        "execution_error_code": (execution.get("error") or {}).get("code"),
        "rule_counts": execution["timing"].get("evaluation", {}).get("counts"),
        "confirmed_bookings_after": (
            sum(b.get("status") == "confirmed" for b in observed["after"])
            if observed["after"] is not None
            else None
        ),
        "evidence_complete": observed["complete"],
        "source": directory.relative_to(ROOT).as_posix(),
        "run_sha256": sha(directory / "run.json"),
        "events_sha256": sha(directory / "events.json"),
        "observations_sha256": sha(directory / "observations.json"),
    }


def comparable(left, right):
    a, b = read(ROOT / left["source"] / "run.json"), read(ROOT / right["source"] / "run.json")
    ma, mb = a["manifest"], b["manifest"]
    differences = []
    for key in (
        "cases",
        "roles",
        "limits",
        "seed",
        "fault_profile",
        "evaluation",
        "pricing",
        "trace_schema",
    ):
        if ma.get(key) != mb.get(key):
            differences.append(key)
    if a["budget"] != b["budget"]:
        differences.append("run_budget")
    ta, tb = ma["target"], mb["target"]
    excluded = {"baseline", "execution_policy", "config_hash", "sources"}
    if {k: v for k, v in ta.items() if k not in excluded} != {
        k: v for k, v in tb.items() if k not in excluded
    }:
        differences.append("target_non_policy_components")
    changed = {
        k
        for k in set(ta["sources"]) | set(tb["sources"])
        if ta["sources"].get(k) != tb["sources"].get(k)
    }
    allowed = {
        "backend/app/agents/graph.py",
        "backend/app/agents/versions.py",
        "backend/app/core/config.py",
    }
    if changed != allowed:
        differences.append("unexpected_source_changes")
    pa, pb = read(ROOT / left["source"] / "plan.json"), read(ROOT / right["source"] / "plan.json")
    if pa.get("global_concurrency") != 1 or pb.get("global_concurrency") != 1:
        differences.append("global_concurrency")
    for name in ("budget.py", "common.py", "episodes.py"):
        if pa["harness"].get(name) != pb["harness"].get(name):
            differences.append("execution_harness:" + name)
    if pa["runner_function_sha256"] != pb["runner_function_sha256"]:
        differences.append("frozen_runner_function")
    return {
        "same_declared_conditions": not differences,
        "differences": differences,
        "source_changes": sorted(changed),
        "underlying_provider_version_pinned": False,
    }


def main():
    catalog = load_catalog()
    jobs = experiment_plan(catalog)["E2"]["episodes"]
    rows = [
        observation(p.parent) for p in sorted((EVIDENCE / "serial-episodes").glob("*/outcome.json"))
    ]
    pilots = [observation(p.parent) for p in sorted((EVIDENCE / "episodes").glob("*/outcome.json"))]
    index = {episode_key(r["episode"]): r for r in rows}
    pairs = []
    for j in jobs:
        if j["version"] != "V0":
            continue
        key = (j["case_key"], j["repeat_no"])
        if ("V0", *key) in index and ("V1", *key) in index:
            a, b = index[("V0", *key)], index[("V1", *key)]
            pairs.append(
                {
                    "case_key": key[0],
                    "repeat_no": key[1],
                    "V0": a["source"],
                    "V1": b["source"],
                    **comparable(a, b),
                    "V0_status": a["status"],
                    "V1_status": b["status"],
                }
            )
    output = EVIDENCE / "analyses" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    cases = {c["id"]: c for c in catalog["cases"]}
    strata = {}
    for dimension, values in (
        ("split", sorted({c["dsl"]["dataset_split"] for c in cases.values()})),
        ("category", sorted({c["dsl"]["category"] for c in cases.values()})),
        ("critical", [False, True]),
    ):
        strata[dimension] = {}
        for value in values:

            def matches(job, dimension=dimension, value=value):
                case = cases[job["case_key"]]
                actual = (
                    case["critical"]
                    if dimension == "critical"
                    else case["dsl"]["dataset_split" if dimension == "split" else "category"]
                )
                return actual == value

            group_jobs = [j for j in jobs if matches(j)]
            group_rows = [r for r in rows if matches(r["episode"])]
            strata[dimension][str(value)] = summarize(group_jobs, group_rows)
    write_new(
        output / "E2.json",
        {
            "dataset_hash": catalog["package_hash"],
            "statistics": summarize(jobs, rows),
            "strata": strata,
            "rows": rows,
            "paired_conditions": pairs,
            "per_case_repeats": [
                {
                    "version": version,
                    "case_key": case_key,
                    "planned_repeat_ids": [
                        j["repeat_no"]
                        for j in jobs
                        if j["version"] == version and j["case_key"] == case_key
                    ],
                    "observations": [
                        {
                            "repeat_no": r["episode"]["repeat_no"],
                            "status": r["status"],
                            "source": r["source"],
                        }
                        for r in rows
                        if r["episode"]["version"] == version
                        and r["episode"]["case_key"] == case_key
                    ],
                }
                for version in ("V0", "V1")
                for case_key in sorted(cases)
            ],
            "diagnostic_pilot_rows": pilots,
            "diagnostic_pilot_not_in_320_denominator": True,
            "human_semantic_results": (
                "pending; do not equate rule pass with full expected behavior"
            ),
        },
    )
    with (output / "E2.csv").open("x", encoding="utf-8-sig", newline="") as file:
        fields = [
            "source",
            "run_id",
            "execution_id",
            "status",
            "task_outcome",
            "attempts",
            "fault_triggered",
            "model_calls_started",
            "known_tokens",
            "unknown_usage_calls",
            "known_peak_estimate_microusd",
            "evidence_complete",
        ]
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                "analysis": str(output),
                "formal_observed": len(rows),
                "formal_planned": 320,
                "pilot_observed": len(pilots),
                "paired": len(pairs),
                "mismatched_pairs": sum(not p["same_declared_conditions"] for p in pairs),
            }
        )
    )


if __name__ == "__main__":
    main()
