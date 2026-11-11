"""Freeze new tasks and reference evidence without running or answering them."""

import argparse
import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.models import TestCase
from benchmarks.t19.collector import exclusive_json, source_manifest

PROTOCOL = PROJECT_ROOT / "docs/fixtures/T19/e4-protocol.json"
ROOT = PROJECT_ROOT / ".local/t19b"


def protocol():
    value = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in value["tasks"]}
    if len(tasks) != 4 or len({t["case_id"] for t in tasks.values()}) != 4:
        raise ValueError("Exactly four distinct new tasks are required")
    for participant, order in value["allocation"].items():
        if participant not in value["participants"] or len(order) != 4:
            raise ValueError("Unknown participant or invalid task count")
        if set(t[0] for t in order) != set(tasks):
            raise ValueError("Each participant must see each new task once")
        for pair in {t["pair"] for t in tasks.values()}:
            if sorted(c for t, c in order if tasks[t]["pair"] == pair) != ["ai", "guide"]:
                raise ValueError("Each pair must contain both conditions")
    return value


def sources():
    # Additive separate package: the original phase-A manifest and files are untouched.
    result = source_manifest()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        result[p.relative_to(PROJECT_ROOT).as_posix()] = canonical_hash(
            p.read_text(encoding="utf-8")
        )
    result[PROTOCOL.relative_to(PROJECT_ROOT).as_posix()] = canonical_hash(protocol())
    return result


def read_cases(engine):
    tasks = protocol()["tasks"]
    result = []
    with Session(engine) as session:
        for task in tasks:
            c = session.scalar(select(TestCase).where(TestCase.id == task["case_id"]))
            if not c or c.dataset_split != "dev" or c.review_status != "approved":
                raise ValueError("Only approved development cases may be frozen")
            if c.case_key != task["case_key"]:
                raise ValueError("Task identity does not match the frozen case")
            result.append(
                {
                    "id": c.id,
                    "case_key": c.case_key,
                    "revision": c.revision,
                    "turns": c.turns_json,
                    "fixture": c.fixture_json,
                    "expected": c.expected_json,
                }
            )
    return result


def prepare(engine):
    value, cases, manifest = protocol(), read_cases(engine), sources()
    frozen = {
        "protocol": value,
        "protocol_hash": canonical_hash(value),
        "case_hash": canonical_hash(cases),
        "source_hash": canonical_hash(manifest),
        "sources": manifest,
        "boundary": "Preparation only; no participant answers, start times or labels",
    }
    frozen["package_hash"] = canonical_hash(frozen)
    folder = ROOT / "packages" / frozen["package_hash"]
    folder.mkdir(parents=True, exist_ok=True)
    for name, contents in (("package.json", frozen), ("reference-private.json", cases)):
        path = folder / name
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != contents:
                raise ValueError("Existing preparation differs; do not replace")
        else:
            exclusive_json(path, contents)
    return folder, frozen


def load_package(folder):
    folder = Path(folder).resolve()
    if not folder.is_relative_to((ROOT / "packages").resolve()):
        raise ValueError("Package must belong to this trial")
    value = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    digest = value["package_hash"]
    if canonical_hash({k: v for k, v in value.items() if k != "package_hash"}) != digest:
        raise ValueError("Package integrity failed")
    if folder.name != digest or canonical_hash(value["protocol"]) != value["protocol_hash"]:
        raise ValueError("Package identity failed")
    cases = json.loads((folder / "reference-private.json").read_text(encoding="utf-8"))
    if canonical_hash(cases) != value["case_hash"]:
        raise ValueError("Frozen reference integrity failed")
    return value


def check_current(folder, engine):
    value = load_package(folder)
    if value["protocol_hash"] != canonical_hash(protocol()):
        raise ValueError("Protocol changed; stop the round, preserve old records")
    if value["source_hash"] != canonical_hash(sources()):
        raise ValueError("Source changed; stop the round, preserve old records")
    if value["case_hash"] != canonical_hash(read_cases(engine)):
        raise ValueError("Case changed; stop the round, preserve old records")
    return value


def text_present(value):
    return bool(value.strip())


class EvidenceAnswer(BaseModel):
    """Separate judgments and evidence; semantic correctness remains human reviewed."""

    model_config = ConfigDict(extra="forbid")
    conclusion: str = Field(min_length=1, max_length=2000)
    evidence_found: bool
    evidence_location: str = Field(max_length=1000)
    evidence_excerpt: str = Field(max_length=2000)
    evidence_reason: str = Field(max_length=1500)
    missing_reason: str = Field(max_length=1500)
    next_action: str = Field(min_length=1, max_length=1000)
    help_count: int = Field(ge=0, le=20, strict=True)
    used_ai: bool
    interrupted: bool
    deviation_note: str = Field(max_length=2000)

    @model_validator(mode="after")
    def evidence_and_deviations(self):
        if not all(text_present(v) for v in (self.conclusion, self.next_action)):
            raise ValueError("Conclusion and next action cannot be whitespace")
        if self.evidence_found:
            if not all(
                text_present(v)
                for v in (self.evidence_location, self.evidence_excerpt, self.evidence_reason)
            ):
                raise ValueError("Record location, excerpt and how it supports the judgment")
        elif not text_present(self.missing_reason):
            raise ValueError("Explain which evidence could not be found")
        if self.help_count or self.used_ai or self.interrupted:
            note = re.sub(r"[\s，。,.!！]", "", self.deviation_note).lower()
            if note in {"", "无", "没有", "none", "无求助", "不适用", "na", "n/a"}:
                raise ValueError("Describe the actual help, AI use or interruption")
        return self


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    folder, value = prepare(create_database_engine(Settings()))
    print(
        json.dumps(
            {
                "package": str(folder),
                "hash": value["package_hash"],
                "status": "prepared_not_executed",
            }
        )
    )


if __name__ == "__main__":
    main()
