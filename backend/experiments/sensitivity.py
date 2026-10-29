"""Separate G1 assistant-reference sensitivity; never replace independent E1 gold."""

import json
from datetime import UTC, datetime

from app.agents.versions import canonical_hash
from app.db.engine import create_database_engine
from experiments.analysis import detection_metrics
from experiments.common import EVIDENCE, ROOT, read, settings, write_new
from experiments.e1 import method_decisions
from experiments.labels import LabelService


def main():
    s = settings()
    engine = create_database_engine(s)
    try:
        pointer = read(EVIDENCE / "human-batches/G1.json")
        batch = LabelService(engine, None, s).read(1, pointer["batch_id"])
        assert batch["seal"] and len(batch["results"]) == 30
        review = read(ROOT / "docs/fixtures/T16/G1-assisted-review.json")
        assert review["original_labels_hash"] == batch["seal"]["labels_hash"]
        assert canonical_hash(batch["seal"]["labels"]) == review["original_labels_hash"]
        rows = []
        for identifier, value in review["samples"].items():
            directory = ROOT / pointer["source_map"][identifier]["directory"]
            execution = read(directory / "run.json")["executions"][0]
            complete = read(directory / "observations.json")["complete"]
            decisions = method_decisions(execution, complete, batch["results"][identifier])
            rows.append(
                {
                    "sample_id": identifier,
                    "original": value["original"]["execution_problem"],
                    "assistant": value["recommendation"]["execution_problem"],
                    "methods": decisions,
                    "source": value["source"],
                }
            )
        metrics = {}
        for reference in ("original", "assistant"):
            metrics[reference] = {}
            for method in ("answer_only", "rule_only", "enhanced"):
                value = detection_metrics(
                    [
                        {
                            "sample_id": r["sample_id"],
                            "human_problem": {
                                "problem": True,
                                "clear": False,
                                "insufficient": None,
                            }[r[reference]],
                            "method_problem": r["methods"][method],
                        }
                        for r in rows
                    ]
                )
                metrics[reference][method] = {
                    k.replace("human", "reference"): v for k, v in value.items()
                }
        path = (
            EVIDENCE
            / "analyses"
            / datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            / "G1-sensitivity.json"
        )
        write_new(
            path,
            {
                "batch_id": batch["batch_id"],
                "samples": 30,
                "unique_cases": 10,
                "original_labels_hash": review["original_labels_hash"],
                "assistant_review_hash": review["review_hash"],
                "metrics": metrics,
                "rows": rows,
                "reference_sources": {
                    "original": "authenticated_independent_human",
                    "assistant": "assistant_posthoc_analysis_requested_by_user_not_independent",
                },
                "boundary": (
                    "G1-only sensitivity, not corrected independent gold or replacement "
                    "for primary E1-60. No new model calls."
                ),
            },
        )
        print(json.dumps({"separate_sensitivity": str(path), "primary_labels_changed": False}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
