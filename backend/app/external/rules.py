"""Platform-side checks over preserved external text, never external self-scoring."""

import hashlib
import json
from pathlib import Path

from app.evaluation.engine import evaluate as evaluate_reply
from app.schemas.assertions import ReplyLiterals


def evaluator_manifest():
    sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    # The shared predicate source must also be frozen: its changes affect the literal checks.
    from app.evaluation.engine import evaluator_manifest as shared_manifest

    shared = shared_manifest()
    version_hash = hashlib.sha256(json.dumps([sha, shared], sort_keys=True).encode()).hexdigest()
    return {
        "kind": "rule",
        "version": "external-dialogue-v1-" + version_hash[:20],
        "source_hash": sha,
        "shared": shared,
        "dsl": "external-dialogue-1",
    }


def evaluate(definitions, bundle, *, goal=None):
    for spec in definitions:
        ReplyLiterals.model_validate(spec)
    if goal is not None:
        raise ValueError("Business goal not supported by external dialogue contract")
    result = evaluate_reply(definitions, bundle, guards=False)
    # Literal pass does not establish a completed business goal or semantic correctness.
    result["task_outcome"] = "unknown"
    return result
