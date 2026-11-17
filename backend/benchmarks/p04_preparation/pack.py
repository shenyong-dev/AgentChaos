"""Validate and preserve a preparation draft without DB, Redis or model access."""

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "docs/fixtures/P04"
INPUTS = ("protocol-draft.json", "tasks-draft.json", "reference-draft.json")
EXPECTED_CASES = {"U01": "T13-N007", "U02": "T13-N009", "U03": "T13-N010"}
FILES = {"protocol.json", "tasks.json", "reference-draft.json", "participant-brief.md"}
SOURCE = "docs/fixtures/T13/review-proposal.md"
SOURCES = (
    SOURCE,
    "backend/app/copilot/sources.py",
    "backend/app/copilot/service.py",
    "backend/app/copilot/prompt.txt",
    "backend/app/guidance/catalog.json",
    "frontend/src/components/CaseReadingGuide.vue",
    "frontend/src/components/CopilotSourceContent.vue",
    "backend/benchmarks/p04_preparation/pack.py",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate(protocol, tasks, reference):
    """Preparation must never be interpreted as paid execution or human results."""
    if (
        protocol["status"] != "preparation_only"
        or protocol["provider_execution_enabled"] is not False
        or protocol["collection_enabled"] is not False
        or protocol["participant_identifiers_created"] is not False
        or any(value != "pending" for value in protocol["prelaunch_gates"].values())
        or protocol["proposed_limits_not_authorized"]["approved"] is not False
    ):
        raise ValueError("Preparation is not execution authorization")
    if (
        protocol["initial_tasks_per_participant"] != 1
        or protocol["record"]["no_prefilled_answers"] is not True
        or protocol["review"]["automatic_semantic_scoring"] is not False
        or protocol["analysis"]["no_ai_gain_or_efficiency_claim"] is not True
    ):
        raise ValueError("Diagnostic design changed")
    rows = tasks["tasks"]
    if len(rows) != 3 or {row["id"]: row["case_key"] for row in rows} != EXPECTED_CASES:
        raise ValueError("Only the three new development tasks belong to this draft")
    if (
        reference["status"] != "pending_independent_human_review"
        or reference["reviewer"] is not None
        or len(reference["references"]) != 3
    ):
        raise ValueError("Do not manufacture a human reference review")
    refs = {row["task_id"]: row for row in reference["references"]}
    if set(refs) != set(EXPECTED_CASES):
        raise ValueError("Reference task mismatch")
    raw = (ROOT / SOURCE).read_text(encoding="utf-8")
    blocks = {
        re.match(r"### (T13-[A-Z]\d+)\b", block)[1]: block
        for block in re.split(r"(?m)(?=^### T13-)", raw)
        if re.match(r"### T13-", block)
    }
    for task in rows:
        block = blocks[task["case_key"]]
        if "分区`dev`" not in block or task["source_split"] != "dev":
            raise ValueError("Protected or non-development data rejected")
        if (
            task["current_case_id"] is not None
            or task["current_revision_hash"] is not None
            or task["binding_status"] != "pending_current_approved_dev_revision"
        ):
            raise ValueError("Current database binding has not been verified")
        source = task["source"]
        if (
            source["file"] != SOURCE
            or source["file_sha256"] != sha(ROOT / SOURCE)
            or source["section"] != task["case_key"]
            or source["excerpt_sha256"] != hashlib.sha256(block.encode()).hexdigest()
        ):
            raise ValueError("Original development source drifted")
        if task["snapshot"]["turns"] != re.findall(r"(?m)^\d+\. (.+)$", block):
            raise ValueError("Original turns changed")
        ref = refs[task["id"]]
        expected = json.loads(re.search(r"数量：\*\*(\[[^\]]+\])\*\*", block)[1])
        if (
            ref["status"] != "developer_reference_draft_not_human_gold"
            or ref["source_excerpt"] != block
            or ref["expected_counts"] != expected
        ):
            raise ValueError("Reference must retain its developer/source boundary")


def load_inputs():
    values = [json.loads((FIXTURES / name).read_text(encoding="utf-8")) for name in INPUTS]
    validate(*values)
    return values


def brief(tasks):
    # Whitelist participant-facing fields. No expected labels or reference checks.
    text = "# P04 单题试用准备稿\n\n尚未开始采集；当前请审阅题目，不填写答案。\n\n"
    text += "正式安排后每人先做一题。只阅读，不创建运行或提交业务；可以随时结束。\n\n"
    for row in tasks["tasks"]:
        text += f"## {row['id']} · {row['title']}\n\n"
        text += f"用例：{row['case_key']}\n\n{row['instruction']}\n\n"
        text += f"入口：{row['start_location']}\n\n"
    text += "## 记录模板\n\n状态：完成 / 卡住 / 不确定 / 提前结束\n\n"
    text += "1. 我的判断：\n2. 证据（页面位置＋一小段原文）：\n3. 下一步点哪里：\n\n"
    text += "计时、求助和调用回执由记录器或观察者另记，不要求参与者重复填表。\n"
    return text


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def prepare(output):
    protocol, tasks, reference = load_inputs()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "protocol.json", protocol)
    write_json(output / "tasks.json", tasks)
    write_json(output / "reference-draft.json", reference)
    (output / "participant-brief.md").write_text(brief(tasks), encoding="utf-8")
    manifest = {
        "schema": "p04-preparation-package-v1",
        "status": "preparation_only",
        "input_hashes": {name: sha(FIXTURES / name) for name in INPUTS},
        "sources": {name: sha(ROOT / name) for name in SOURCES},
        "files": {name: sha(output / name) for name in sorted(FILES)},
        "participant_records": 0,
        "provider_calls": 0,
        "boundary": "Preparation hash only, not a frozen executed study or authorization",
    }
    manifest["package_hash"] = digest(manifest)
    write_json(output / "manifest.json", manifest)
    return manifest


def verify(output):
    output = Path(output)
    value = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if value["package_hash"] != digest({k: v for k, v in value.items() if k != "package_hash"}):
        raise ValueError("Preparation manifest changed")
    if (
        value["schema"] != "p04-preparation-package-v1"
        or value["status"] != "preparation_only"
        or value["participant_records"] != 0
        or value["provider_calls"] != 0
    ):
        raise ValueError("Preparation must not contain claimed execution results")
    if set(value["files"]) != FILES or {p.name for p in output.iterdir()} != FILES | {
        "manifest.json"
    }:
        raise ValueError("Unexpected or missing package member")
    for name, expected in value["files"].items():
        if sha(output / name) != expected:
            raise ValueError("Saved preparation member changed")
    if value["sources"] != {name: sha(ROOT / name) for name in SOURCES}:
        raise ValueError("Current preparation sources drifted")
    if value["input_hashes"] != {name: sha(FIXTURES / name) for name in INPUTS}:
        raise ValueError("Current preparation draft differs")
    protocol, tasks, reference = [
        json.loads((output / name).read_text(encoding="utf-8"))
        for name in ("protocol.json", "tasks.json", "reference-draft.json")
    ]
    validate(protocol, tasks, reference)
    if (output / "participant-brief.md").read_text(encoding="utf-8") != brief(tasks):
        raise ValueError("Participant brief differs from the whitelisted projection")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--check", action="store_true")
    choice.add_argument("--output", type=Path)
    choice.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.check:
        load_inputs()
        value = {"status": "preparation_only", "tasks": 3}
    else:
        record = verify(args.verify) if args.verify else prepare(args.output)
        value = {"status": record["status"], "package_hash": record["package_hash"]}
    print(json.dumps({**value, "participant_records": 0, "provider_calls": 0}))


if __name__ == "__main__":
    main()
