"""Real final-answer-only opinions. Labels are sealed first and never sent to the model."""

import argparse
import json
from datetime import UTC, datetime
from uuid import UUID, uuid5

from langchain_core.messages import HumanMessage, SystemMessage
from redis import Redis

from app.agents.gateway import ModelGateway
from app.agents.versions import canonical_hash
from app.db.engine import create_database_engine
from app.judging.engine import interpret, manifest
from experiments.budget import FormalJudgeBudget
from experiments.common import EVIDENCE, read, settings, write_new
from experiments.labels import LabelService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", choices=["C", "G1", "G2"], required=True)
    args = parser.parse_args()
    s = settings().model_copy(update={"judge_allow_paid_model_calls": True})
    engine = create_database_engine(s)
    redis = Redis.from_url(s.redis_url, socket_timeout=15)
    service = LabelService(engine, redis, s)
    batch = read(EVIDENCE / "human-batches" / (args.batch + ".json"))["batch_id"]
    current = service.read(1, batch)
    if not current["seal"] or len(current["labels"]) != 30:
        raise SystemExit("Real independent labels are not sealed; no judge calls permitted.")
    assert canonical_hash(current["seal"]["labels"]) == current["seal"]["labels_hash"]
    if args.batch != "C":
        calibration = service.read(1, read(EVIDENCE / "human-batches/C.json")["batch_id"])
        assert calibration["seal"] and len(calibration["results"]) == 30
        # Independent gold for all 60 must precede either set of method opinions.
        for name in ("G1", "G2"):
            gold = service.read(1, read(EVIDENCE / "human-batches" / (name + ".json"))["batch_id"])
            assert gold["seal"] and len(gold["labels"]) == 30
    protocol = manifest(s)
    protocol.update(
        T16_judge_view="final_answer_only",
        calibration_batch=read(EVIDENCE / "human-batches/C.json")["batch_id"],
    )
    samples = current["frozen"]["dataset"]["samples"]
    folder = EVIDENCE / "judge" / args.batch
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "protocol.json").exists():
        write_new(folder / "protocol.json", protocol)
        service._append(1, batch, "judge_result", protocol, "protocol")
    assert read(folder / "protocol.json") == protocol
    try:
        for sample in samples:
            identifier = sample["id"]
            current = service.read(1, batch)
            if identifier in current["results"]:
                continue
            if any(
                v.get("sample_id") == identifier and v["kind"] == "judge_call"
                for v in current["evidence"]
            ):
                raise SystemExit("Existing incomplete call needs audit; never silently pay again.")
            # For gold batches the human saw SQL/tools, but the judge receives the exact
            # same limited view as calibration. No gold labels or rule verdicts enter this object.
            original = sample["payload"]
            payload = {k: original[k] for k in ("dialogue", "reply", "dialogue_scope")}
            payload["evidence"] = []
            sample_file = folder / (identifier + "-input.json")
            write_new(sample_file, payload)

            def record(stage, value, sample_id=identifier):
                service._append(1, batch, "judge_call", value, stage, sample_id=sample_id)

            budget = FormalJudgeBudget(redis, s, str(batch), record)
            gateway = ModelGateway(s, budget, role="judge", audit_directory=folder / "provider")
            messages = [
                SystemMessage(content=protocol["prompt"]),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
            record(
                "invoking",
                {
                    "sample_id": identifier,
                    "payload_hash": canonical_hash(payload),
                    "judge_view": "final_answer_only",
                    "independent_labels_sealed": True,
                },
            )
            try:
                message, provider = gateway.invoke(messages, [])
                record("provider", provider)
                try:
                    raw = json.loads(message.content)
                except (ValueError, TypeError):
                    raw = {"invalid_json": True, "content": message.content}
                result = interpret(raw, payload)
            except Exception as exc:
                record(
                    "error",
                    {
                        "exception_type": type(exc).__name__,
                        "code": getattr(exc, "code", "provider_error"),
                    },
                )
                result = {
                    "status": "error",
                    "reason": getattr(exc, "code", "provider_error"),
                    "needs_review": True,
                }
            result.update(
                sample_id=identifier,
                sample_hash=sample["sample_hash"],
                manifest_hash=canonical_hash(protocol),
                role="judge",
                mode="real_provider",
                judge_view="final_answer_only",
                finished_at=datetime.now(UTC).isoformat(),
            )
            result["result_hash"] = canonical_hash(result)
            service._append(
                1,
                batch,
                "judge_result",
                result,
                "result",
                sample_id=identifier,
                request_id=str(uuid5(UUID(batch), identifier + ":T16-final-only")),
                actor=1,
            )
            write_new(folder / (identifier + "-result.json"), result)
            print(json.dumps({"sample": identifier, "status": result["status"]}), flush=True)
        final = service.read(1, batch)
        write_new(
            folder / "summary.json",
            {
                "batch_id": batch,
                "labels_hash": final["seal"]["labels_hash"],
                "statistics": final["statistics"],
                "results": final["results"],
                "boundary": (
                    "Original independent labels unchanged. Same-provider opinions; "
                    "final-only view, not full SQL/trace judgment."
                ),
            },
        )
    finally:
        redis.close()
        engine.dispose()


if __name__ == "__main__":
    main()
