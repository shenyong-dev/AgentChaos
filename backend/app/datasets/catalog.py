"""Static validation only; never invokes the tested Agent or its decision functions."""

import json
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.schemas.contracts import TestCaseDraft
from app.schemas.faults import FaultSpec

PATH = PROJECT_ROOT / "datasets/v1.1/candidate.json"
COUNTS = {
    "normal": 20,
    "tool_execution": 25,
    "memory_and_tool": 25,
    "retrieval": 20,
    "security": 15,
    "robustness": 15,
}
SPLITS = {"dev": 60, "calibration": 30, "holdout": 30}


def audit(value):
    if (
        canonical_hash({k: v for k, v in value.items() if k != "package_hash"})
        != value["package_hash"]
    ):
        raise ValueError("Package hash mismatch")
    cases = value["cases"]
    if len(cases) != 120 or Counter(c["dsl"]["category"] for c in cases) != COUNTS:
        raise ValueError("120 cases and exact six-category coverage required")
    if Counter(c["dsl"]["dataset_split"] for c in cases) != SPLITS:
        raise ValueError("60/30/30 split required")
    critical = set(value["critical_cases"])
    if len(critical) != 20 or critical != {c["id"] for c in cases if c["critical"]}:
        raise ValueError("Critical selection mismatch")
    ids, texts, families, matrix = set(), set(), defaultdict(set), defaultdict(Counter)
    for c in cases:
        d = TestCaseDraft.model_validate(c["dsl"])
        if c["id"] in ids or c["id"] != d.case_key or d.review_status != "draft":
            raise ValueError("Duplicate key or client pre-approval")
        ids.add(c["id"])
        if c["case_hash"] != canonical_hash({k: v for k, v in c.items() if k != "case_hash"}):
            raise ValueError("Case hash mismatch")
        families[d.family_key].add(d.dataset_split)
        matrix[d.category][d.dataset_split] += 1
        profile = value["knowledge"][c["knowledge_profile"]]
        # Identical text under genuinely different frozen KB/fault conditions is a different test.
        normalized = "".join(
            x
            for x in unicodedata.normalize(
                "NFKC", json.dumps(d.turns, default=lambda x: x.model_dump(), ensure_ascii=False)
            ).casefold()
            if x.isalnum()
        )
        fp = canonical_hash([normalized, profile, c["fault"]])
        if fp in texts:
            raise ValueError("Duplicate normalized input and environment")
        texts.add(fp)
        expected = c["independent_expectation"]
        initial = c["initial_state"]
        if (
            initial["fixture"] != "booking-default-v1"
            or initial["active_bookings"]
            or initial["drafts"]
        ):
            raise ValueError("Only the actual empty fixture is supported")
        counts = expected["active_booking_counts"]
        asserted = [x.model_dump() for x in d.assertions if x.type == "booking_count"]
        if not counts or len(set(counts)) != len(counts) or any(n < 0 for n in counts):
            raise ValueError("Independent booking counts required")
        if len(counts) == 1 and asserted != [{"type": "booking_count", "equals": counts[0]}]:
            raise ValueError("Count assertion contradicts independent expectation")
        if len(counts) > 1 and asserted:
            raise ValueError(
                "Permitted degraded and successful outcomes cannot be one forced count"
            )
        if c["fault"]:
            FaultSpec.model_validate(c["fault"])
        final = expected["final_booking"]
        if final:
            # Independent arithmetic over public constants; no BookingService calls.
            caps = {"A101": (20, "21:00"), "B201": (40, "21:00"), "C301": (80, "18:00")}
            capacity, closing = caps[final["room_code"]]
            start = datetime.fromisoformat(final["date"] + "T" + final["start_time"] + ":00+08:00")
            end = datetime.fromisoformat(final["date"] + "T" + final["end_time"] + ":00+08:00")
            if not (
                1 <= final["attendees"] <= capacity
                and timedelta(minutes=30) <= end - start <= timedelta(minutes=120)
                and start.minute % 30 == end.minute % 30 == 0
                and "09:00" <= final["start_time"] < final["end_time"] <= closing
                and d.reference_clock <= start < end <= d.reference_clock + timedelta(days=7)
            ):
                raise ValueError(
                    "Independently declared successful booking violates public fixture"
                )
        if not expected["reason"] or d.reference_clock.isoformat() != value["reference_clock"]:
            raise ValueError("Explicit reference clock and independent rationale required")
    if any(len(x) != 1 for x in families.values()):
        raise ValueError("A family crosses dataset splits")
    if value.get("revision_of"):
        parent = json.loads((PROJECT_ROOT / "datasets/v1/candidate.json").read_bytes())
        if value["revision_of"] != parent["package_hash"]:
            raise ValueError("Revision parent mismatch")
        before = {c["id"]: c for c in parent["cases"]}
        changed = {c["id"] for c in cases if c != before[c["id"]]}
        if changed != {"T13-M022", "T13-M023", "T13-S014"}:
            raise ValueError("Only the three explicitly reviewed replacements may change")
        known = {
            "t13-conditional-edit-no-grant",
            "t13-long-reversal",
            "t13-retrieval-versus-user-refusal",
        }
        if any(
            c["dsl"]["family_key"] in known or c["dsl"]["revision"] != 2
            for c in cases
            if c["id"] in changed
        ):
            raise ValueError("Known legacy holdout families require new revision and new mechanism")
    return {
        "cases": 120,
        "categories": COUNTS,
        "splits": SPLITS,
        "families": len(families),
        "family_splits": {k: next(iter(v)) for k, v in families.items()},
        "coverage": dict(matrix),
        "critical": 20,
        "normalized_duplicates": 0,
        "boundary": (
            "Static arithmetic/schema/family checks, not Agent executions or human approval."
        ),
    }


def load_catalog():
    try:
        value = json.loads(PATH.read_bytes())
        audit(value)
        if json.loads((PATH.parent / "knowledge.json").read_bytes()) != value["knowledge"]:
            raise ValueError("Published knowledge projection differs from package")
        return value
    except (KeyError, ValueError, OSError, TypeError):
        raise AgentError(
            "dataset_invalid", "固定数据集缺失、哈希/覆盖或预期检查不符。", 503
        ) from None


def experiment_plan(value):
    """Executable episode selection; no invented V1 or future budget authorization."""
    critical = set(value["critical_cases"])
    episodes = []
    for version in ("V0", "V1"):
        for case in value["cases"]:
            for repeat in range(1, 4 if case["id"] in critical else 2):
                episodes.append(
                    {
                        "version": version,
                        "case_key": case["id"],
                        "repeat_no": repeat,
                        "seed": 13028 + repeat - 1,
                        "agent_cap_microusd": case["agent_cap_microusd"],
                        "fault_hash": canonical_hash(case["fault"]) if case["fault"] else None,
                        "knowledge_profile": case["knowledge_profile"],
                    }
                )
    cap = sum(e["agent_cap_microusd"] for e in episodes)
    return {
        "schema_version": "T13-experiments-v1",
        "dataset_hash": value["package_hash"],
        "E2": {
            "episodes": episodes,
            "counts": {"V0": 160, "V1": 160},
            "comparison_controls": [
                "model",
                "knowledge",
                "fixture",
                "fault",
                "seed",
                "limits",
                "case_hash",
            ],
            "V1": "UNREGISTERED: requires explicit reviewed implementation; never alias V0",
        },
        "E1": {
            "source": (
                "The 20 critical V0 cases, three planned repeats = 60 fr"
                "ozen trajectories; no second Agent execution"
            ),
            "judge_views": (
                "One final-answer-only judge opinion reused in both meth"
                "ods; enhanced method adds objective trace/state checks"
            ),
            "judge_calls": 60,
            "additional_independent_view_calibration_calls": 30,
            "calibration_gate": (
                "Final-only view differs from T12 full-evidence view: mu"
                "st independently label/calibrate before claiming E1 qua"
                "lity"
            ),
            "human_ground_truth": (
                "60 frozen trajectories require real independent human j"
                "udgments before looking at method outcomes; not prefill"
                "ed T13 labels"
            ),
        },
        "execution_granularity": (
            "One episode per one-case run, repetitions=1; plan repea"
            "t_no is recorded separately and never conflated with in"
            "frastructure attempts"
        ),
        "role_caps_microusd": {"agent": cap, "case_generator": 0, "judge": 1_350_000},
        "total_planning_cap_microusd": cap + 1_350_000,
        "paid_calls_this_task": 0,
        "paid_authorization": (
            "NOT_GRANTED_BY_PLAN_APPROVAL; activation belongs to authorized formal-experiment task"
        ),
        "cost_boundary": (
            "Per-episode occupied caps, not predicted cost or a curr"
            "ent provider price. Known usage needed for actual estim"
            "ates."
        ),
        "E3": "P1 not activated",
        "untriggered_fault": "Report separately; never count as fault-tolerance success",
        "manual_criteria": (
            "Retrieval citation/applicability, semantic claims and a"
            "ttack behavior remain explicit human review; not all au"
            "tomated by DSL1.1"
        ),
    }
