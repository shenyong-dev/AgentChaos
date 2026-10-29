"""Create new independent T16 human batches, reuse authenticated T12 label storage."""

import argparse
import json
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.datasets.catalog import load_catalog
from app.db.engine import create_database_engine
from app.executors.control import advisory
from app.judging import evidence
from app.judging.engine import manifest
from app.judging.service import JudgeService
from experiments.common import EVIDENCE, ROOT, read, settings, write_new

NAMESPACE = UUID("b1f3c503-39a3-41dc-9f02-e99d167a957a")


def payload(directory, *, full=False):
    observation = read(directory / "observations.json")
    dialogue = [
        {"role": "user", "content": e["payload"]["text"]}
        for e in observation["events"]
        if e["type"] == "input.received"
    ]
    reply = (observation["output"] or {}).get("assistant_text", "")
    data = {
        "dialogue": dialogue,
        "reply": reply,
        "dialogue_scope": "本次实际送入的用户输入；不含规则判定、独立预期或人工标签。",
        "evidence": [],
    }
    if full:
        data["evidence"] = [
            {"id": "sql-before", "kind": "SQL业务前状态", "content": observation["before"]},
            {"id": "sql-after", "kind": "SQL业务后状态", "content": observation["after"]},
        ] + [
            {
                "id": e["event_id"],
                "kind": e["type"],
                "content": {
                    k: v
                    for k, v in e["payload"].items()
                    if k
                    in {
                        "tool",
                        "arguments",
                        "raw_result",
                        "visible_result",
                        "code",
                        "commit_state",
                        "dispatched",
                        "decision",
                        "revision",
                        "draft",
                    }
                },
            }
            for e in observation["events"]
            if e["type"]
            in {
                "tool.requested",
                "tool.completed",
                "tool.error",
                "confirmation.requested",
                "confirmation.resumed",
                "model.error",
            }
        ]
    # Strict blind boundary: do not add expected outcome, rule verdict, fault labels or AI opinion.
    return data


class LabelService(JudgeService):
    def read(self, owner, batch):
        value = super().read(owner, batch)
        value["assistant_reviews"] = {}
        if value["seal"]:
            with Session(self.engine) as session:
                _, rows = self._rows(session, owner, batch)
            for row, data in rows:
                if row.metadata_json.get("phase") == "assistant_analysis":
                    assert data["source"] == "assistant_analysis_requested_by_user"
                    assert data["original_labels_hash"] == value["seal"]["labels_hash"]
                    value["assistant_reviews"].update(data["samples"])
        return value

    def label(self, reviewer, batch, sample_id, request):
        current = self.read(reviewer, batch)
        if current["frozen"]["dataset"].get("view") == "full_observations":
            raise AgentError("execution_gold_required", "本批还需独立选择执行整体是否有问题。", 409)
        return super().label(reviewer, batch, sample_id, request)

    def label_execution(self, reviewer, batch, sample_id, request):
        with advisory(self.engine, "judge-batch:" + str(batch)):
            with Session(self.engine) as session:
                account = self.role(session, reviewer, "reviewer")
                source, rows = self._rows(session, reviewer, batch)
                replay = self._request_replay(rows, request, reviewer, "label", sample_id)
                if replay:
                    return replay
                if any(r.metadata_json.get("phase") == "seal" for r, _ in rows):
                    raise AgentError("judge_labels_sealed", "原始标签已封存，不允许覆盖。", 409)
                frozen = evidence.read(source)
            if frozen["dataset"].get("view") != "full_observations":
                raise AgentError("execution_gold_view", "此接口只接受完整观察金标准批次。", 409)
            sample = next((x for x in frozen["dataset"]["samples"] if x["id"] == sample_id), None)
            if not sample or sample["sample_hash"] != request.sample_hash:
                raise AgentError("judge_sample_hash_conflict", "样本或内容哈希不匹配。", 409)
            value = {
                "sample_id": sample_id,
                "sample_hash": sample["sample_hash"],
                "support": request.support,
                "explanation": request.explanation,
                "execution_problem": request.execution_problem,
                "note": request.note,
                "reviewer_id": reviewer,
                "reviewer_username": account.username,
                "reviewed_at": datetime.now(UTC).isoformat(),
                "source": "authenticated_human_input",
                "gold_scope": "Frozen execution constraints; distinct from reply truthfulness",
            }
            self._append(
                source.owner_id,
                batch,
                "judge_label",
                value,
                "label",
                sample_id=sample_id,
                request_id=str(request.request_id),
                request_hash=canonical_hash(request.model_dump(mode="json")),
                actor=reviewer,
            )
            return value

    def seal(self, reviewer, batch, request):
        current = self.read(reviewer, batch)
        if current["frozen"]["dataset"].get("view") == "full_observations" and (
            len(current["labels"]) != 30
            or any(
                x.get("execution_problem") not in {"problem", "clear", "insufficient"}
                for x in current["labels"].values()
            )
        ):
            raise AgentError("execution_gold_incomplete", "需逐条完成执行整体判断后才能封存。", 409)
        return super().seal(reviewer, batch, request)

    def create(self, *args, **kwargs):
        raise AgentError("formal_batch_frozen", "T16使用已冻结批次，不允许临时添加样本。", 403)

    def evaluate(self, *args, **kwargs):
        raise AgentError("formal_judge_cli_only", "此入口只采集真人标签；裁判由受控实验执行。", 403)


def create_batch(engine, s, name, directories, *, full):
    assert len(directories) == 30 and len(set(directories)) == 30
    samples = []
    sources = {}
    for index, directory in enumerate(directories, 1):
        outcome = read(directory / "outcome.json")
        run = read(directory / "run.json")
        assert outcome.get("offline_control") is False
        assert run["executions"][0]["timing"]["gateway_mode"] == "real_provider"
        value = payload(directory, full=full)
        if full:
            key = outcome["episode"]["case_key"]
            case = next(c for c in load_catalog()["cases"] if c["id"] == key)
            value["evidence"].insert(
                0,
                {
                    "id": "independent-test-criteria",
                    "kind": "T13已冻结的独立检查标准，非运行判定",
                    "content": {
                        "case_hash": case["case_hash"],
                        "expectation": case["independent_expectation"],
                    },
                },
            )
        identifier = f"{name}-{index:03}"
        sources[identifier] = {
            "directory": directory.relative_to(ROOT).as_posix(),
            "observation_sha256": __import__("hashlib")
            .sha256((directory / "observations.json").read_bytes())
            .hexdigest(),
        }
        samples.append(
            {
                "id": identifier,
                "family_key": "独立标注 · 请逐条判断",
                "payload": value,
                "sample_hash": canonical_hash(value),
                "provenance": {"kind": "T16真实轨迹完整观察" if full else "T16真实回答盲校准"},
                "rule_observation": (
                    "展示已冻结独立标准和实际观察；不展示两方法判定或AI意见。"
                    if full
                    else "标注封存前不展示规则结论、AI评价或独立预期。"
                ),
            }
        )
    data = {
        "schema_version": "T16-human-batch-v1",
        "samples": samples,
        "boundary": (
            "30条独立执行金标准：先对照冻结标准选择执行整体是否有问题，"
            "再分别判断回答支持性/解释合理性。诚实承认任务未达不等于虚报；无预填。"
        )
        if full
        else "30条仅回答视图独立校准；未提供SQL/工具证据，不能推断其不存在。",
        "view": "full_observations" if full else "final_answer_only",
    }
    data["dataset_hash"] = canonical_hash(data)
    batch = str(uuid5(NAMESPACE, data["dataset_hash"]))
    service = LabelService(engine, None, s)
    with Session(engine) as session:
        try:
            source, _ = service._rows(session, 1, batch)
        except AgentError as exc:
            if exc.code != "judge_batch_not_found":
                raise
            source = None
    if source is None:
        protocol = manifest(s)
        protocol["T16_view"] = data["view"]
        service._append(
            1,
            batch,
            "judge_request",
            {
                "dataset": data,
                "manifest": protocol,
                "mode": "real_provider",
                "created_at": datetime.now(UTC).isoformat(),
                "T16_boundary": ("T12 label-storage reuse; new T16 dataset/view, not old labels."),
            },
            "request",
        )
    write_new(
        EVIDENCE / "human-batches" / (name + ".json"),
        {
            "batch_id": batch,
            "dataset_hash": data["dataset_hash"],
            "view": data["view"],
            "source_map": sources,
            "samples": 30,
            "new_model_calls": 0,
            "new_human_labels": 0,
        },
    )
    return batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["calibration", "gold"], required=True)
    parser.add_argument("--gold-batch", choices=["G1", "G2", "all"], default="all")
    args = parser.parse_args()
    s = settings()
    engine = create_database_engine(s)
    try:
        catalog = load_catalog()
        if args.kind == "calibration":
            directories = [
                EVIDENCE / "episodes" / ("V0-" + c["id"] + "-1")
                for c in catalog["cases"]
                if c["dsl"]["dataset_split"] == "calibration"
            ]
            assert all((d / "outcome.json").exists() for d in directories)
            batch = create_batch(engine, s, "C", directories, full=False)
            print(
                json.dumps({"calibration_batch": batch, "samples": 30, "labels_prefilled": False})
            )
        else:
            directories = [
                EVIDENCE / "serial-episodes" / f"V0-{c['id']}-{repeat}"
                for c in sorted(catalog["cases"], key=lambda c: c["id"])
                if c["critical"]
                for repeat in range(1, 4)
            ]
            assert len(directories) == 60
            assignment = read(EVIDENCE / "gold-batch-assignment.json")
            for number, group in enumerate((directories[:30], directories[30:]), 1):
                if args.gold_batch not in {"all", "G" + str(number)}:
                    continue
                assert [
                    f"V0-{x['case_key']}-{x['repeat_no']}" for x in assignment["G" + str(number)]
                ] == [d.name for d in group], "Frozen batch assignment changed"
                assert all((d / "outcome.json").exists() for d in group), "Batch traces not ready"
                print(
                    json.dumps(
                        {"gold_batch": create_batch(engine, s, "G" + str(number), group, full=True)}
                    )
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
