"""Read-only E1 comparison after independent gold; never synthesize human judgments."""

import json
from datetime import UTC, datetime

from app.agents.versions import canonical_hash
from app.db.engine import create_database_engine
from app.judging.engine import agreement
from experiments.analysis import detection_metrics
from experiments.common import EVIDENCE, ROOT, read, settings, sha, write_new
from experiments.labels import LabelService


def method_decisions(execution, complete, opinion):
    answer = {"passed": False, "failed": True}.get((opinion or {}).get("status"))
    evaluation = execution.get("timing", {}).get("evaluation", {})
    rule = None
    if complete and execution["status"] in {"passed", "failed"}:
        rule = {"passed": False, "failed": True}.get(evaluation.get("verdict"))
    enhanced = True if rule is True else answer if rule is False else None
    return {"answer_only": answer, "rule_only": rule, "enhanced": enhanced}


def main():
    s = settings()
    engine = create_database_engine(s)
    service = LabelService(engine, None, s)
    rows, humans, opinions, seals = [], {}, {}, {}
    try:
        for name in ("G1", "G2"):
            pointer = read(EVIDENCE / "human-batches" / (name + ".json"))
            batch = service.read(1, pointer["batch_id"])
            seal = batch["seal"]
            assert seal and len(seal["labels"]) == 30, "Real independent seal required"
            assert canonical_hash(seal["labels"]) == seal["labels_hash"]
            seals[name] = {"batch_id": pointer["batch_id"], "labels_hash": seal["labels_hash"]}
            for identifier, label in seal["labels"].items():
                assert label["source"] == "authenticated_human_input"
                human = {"problem": True, "clear": False, "insufficient": None}[
                    label["execution_problem"]
                ]
                source = pointer["source_map"][identifier]
                directory = ROOT / source["directory"]
                assert sha(directory / "observations.json") == source["observation_sha256"]
                run = read(directory / "run.json")
                observation = read(directory / "observations.json")
                outcome = read(directory / "outcome.json")
                assert outcome["episode"]["version"] == "V0"
                opinion = batch["results"].get(identifier)
                decisions = method_decisions(run["executions"][0], observation["complete"], opinion)
                rows.append(
                    {
                        "sample_id": identifier,
                        "episode": outcome["episode"],
                        "human_problem": human,
                        "methods": decisions,
                        "judge_status": (opinion or {}).get("status", "not_evaluated"),
                        "source": source,
                        "label": label,
                        "judge_result_hash": (opinion or {}).get("result_hash"),
                    }
                )
                humans[identifier] = label
                if opinion:
                    opinions[identifier] = opinion
        assert len(rows) == 60 and len({r["episode"]["case_key"] for r in rows}) == 20
        output = EVIDENCE / "analyses" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        write_new(
            output / "E1.json",
            {
                "protocol_sha256": sha(ROOT / "docs/fixtures/T16/E1-protocol.md"),
                "seals": seals,
                "planned_trajectories": 60,
                "unique_cases": 20,
                "opinions_received": len(opinions),
                "metrics": {
                    method: detection_metrics(
                        [
                            {
                                "sample_id": r["sample_id"],
                                "human_problem": r["human_problem"],
                                "method_problem": r["methods"][method],
                            }
                            for r in rows
                        ]
                    )
                    for method in ("answer_only", "rule_only", "enhanced")
                },
                "reply_dimensions": agreement(humans, opinions),
                "rows": rows,
                "boundary": (
                    "Original independent labels; repeated curated cases, no significance claim."
                ),
            },
        )
        print(json.dumps({"analysis": str(output), "opinions": len(opinions)}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
