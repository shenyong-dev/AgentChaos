"""Pure output validation and calibration statistics. No model or label-derived decisions."""

import hashlib
from pathlib import Path

from app.agents.versions import canonical_hash
from app.schemas.judging import Judgment

THRESHOLD = 0.8


def manifest(settings):
    root = Path(__file__).parent
    prompt = (root / "prompt.txt").read_text(encoding="utf-8")
    return {
        "version": "T12-v1",
        "role": "judge",
        "model": settings.judge_model,
        "thinking": settings.judge_thinking,
        "max_output_tokens": settings.judge_max_output_tokens,
        "threshold": THRESHOLD,
        "prompt": prompt,
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.py")},
        "schema_hash": canonical_hash(Judgment.model_json_schema()),
        "automatic_retries": 0,
        "limits": {"calls": 30, "tokens": 2_000_000, "microusd": 500_000, "per_sample_seconds": 45},
        "boundary": "Semantic assistance only; rule and human decisions remain separate.",
    }


def interpret(raw, payload):
    try:
        value = Judgment.model_validate(raw).model_dump(mode="json")
    except ValueError:
        return {
            "status": "error",
            "reason": "judge_schema_invalid",
            "raw": raw,
            "needs_review": True,
        }
    available = {"reply", "dialogue"} | {e["id"] for e in payload["evidence"]}
    if any(
        ref not in available
        for k in ("support", "explanation")
        for ref in value[k]["evidence_refs"]
    ):
        return {
            "status": "error",
            "reason": "judge_unknown_reference",
            "raw": raw,
            "needs_review": True,
        }
    labels = [value[k]["label"] for k in ("support", "explanation")]
    uncertain = (
        value["needs_review"]
        or "insufficient" in labels
        or any(value[k]["confidence"] < THRESHOLD for k in ("support", "explanation"))
    )
    conclusion = (
        "uncertain"
        if uncertain
        else "problematic"
        if ("contradicted" in labels or "unreasonable" in labels)
        else "acceptable"
    )
    if value["conclusion"] != conclusion:
        return {
            "status": "error",
            "reason": "judge_contradictory_output",
            "raw": raw,
            "needs_review": True,
        }
    return {
        "status": "inconclusive"
        if uncertain
        else "failed"
        if conclusion == "problematic"
        else "passed",
        "assessment": value,
        "needs_review": uncertain,
        "cause_kind": "candidate_only",
    }


def agreement(human, ai):
    dimensions = {}
    for dimension in ("support", "explanation"):
        pairs = [
            (human[k][dimension], v["assessment"][dimension]["label"])
            for k, v in ai.items()
            if k in human and v.get("assessment")
        ]
        table = {}
        for h, a in pairs:
            table.setdefault(h, {})[a] = table.get(h, {}).get(a, 0) + 1
        count = len(pairs)
        observed = sum(h == a for h, a in pairs) / count if count else None
        labels = set(x for pair in pairs for x in pair)
        expected = (
            sum(
                sum(h == label for h, a in pairs) * sum(a == label for h, a in pairs)
                for label in labels
            )
            / (count * count)
            if count
            else None
        )
        dimensions[dimension] = {
            "n": count,
            "agreement": observed,
            "confusion": table,
            "cohen_kappa": (observed - expected) / (1 - expected)
            if count and expected != 1
            else None,
            "kappa_boundary": (
                "One human vs same-provider judge, correlated curated samples; "
                "not inter-human reliability or holdout accuracy."
            ),
        }
    evaluated = [(k, v) for k, v in ai.items() if k in human and v.get("assessment")]
    return {
        "dimensions": dimensions,
        "received": len(ai),
        "failure_rate": sum(v.get("status") == "error" for v in ai.values()) / len(ai)
        if ai
        else None,
        "needs_review": [
            k
            for k, v in ai.items()
            if v.get("needs_review")
            or v.get("status") == "error"
            or k in human
            and v.get("assessment")
            and any(human[k][d] != v["assessment"][d]["label"] for d in dimensions)
        ],
        "usable_assessments": len(evaluated),
        "confident_results": sum(v.get("status") in ("passed", "failed") for v in ai.values()),
        "false_accepts": [
            k
            for k, v in evaluated
            if v.get("status") == "passed"
            and (human[k]["support"] != "supported" or human[k]["explanation"] != "reasonable")
        ],
        "false_rejects": [
            k
            for k, v in evaluated
            if v.get("status") == "failed"
            and human[k]["support"] == "supported"
            and human[k]["explanation"] == "reasonable"
        ],
    }
