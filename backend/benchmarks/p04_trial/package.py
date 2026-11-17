"""Bind approved development revisions and immutable sources for this new pilot."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT
from app.models import TestCase
from app.workbench.service import WorkbenchService
from benchmarks.t19.collector import clock, exclusive_json

ROOT = PROJECT_ROOT / ".local/p04-pilot-20261115"
AUTHORIZATION = "P04-20261115-copilot-open-1"
CASE_KEYS = ("T13-N007", "T13-N009", "T13-N010")


def sources():
    paths = []
    for folder in (
        "backend/app",
        "backend/reference_agents",
        "frontend/src",
        "backend/benchmarks/p04_trial",
    ):
        paths.extend(
            p
            for p in (PROJECT_ROOT / folder).rglob("*")
            if p.is_file() and p.suffix in {".py", ".json", ".txt", ".css", ".ts", ".vue", ".svg"}
        )
    paths.extend(
        PROJECT_ROOT / name
        for name in (
            "backend/uv.lock",
            "frontend/package-lock.json",
            "backend/benchmarks/t19/collector.py",
        )
    )
    return {
        p.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def cases(engine, settings):
    draft = json.loads(
        (PROJECT_ROOT / "docs/fixtures/P04/tasks-draft.json").read_text(encoding="utf-8")
    )
    result = []
    reader = WorkbenchService(engine, settings)
    with Session(engine) as session:
        for task in draft["tasks"]:
            if task["case_key"] not in CASE_KEYS:
                raise ValueError("Unexpected pilot case")
            row = session.scalar(select(TestCase).where(TestCase.case_key == task["case_key"]))
            if not row or row.dataset_split != "dev" or row.review_status != "approved":
                raise ValueError("Pilot requires an approved development revision")
            snapshot = reader.case(1, row.id)
            if [turn["user"] for turn in snapshot["guidance"]["turns"]] != task["snapshot"][
                "turns"
            ]:
                raise ValueError("Development turns differ from the prepared draft")
            result.append(
                {
                    "id": task["id"],
                    "title": task["title"],
                    "instruction": task["instruction"],
                    "case_id": row.id,
                    "case_key": row.case_key,
                    "snapshot": snapshot,
                }
            )
    return result


def prepare(engine, settings):
    value = {
        "schema": "p04-exploratory-pilot-v1",
        "created_at": clock(),
        "owner": 1,
        "mode": "exploratory_usability_without_correctness_scoring",
        "reference_review": "pending_human_review; developer reference is not gold",
        "limits": {
            "authorization_id": AUTHORIZATION,
            "role": "copilot",
            "cumulative_money": None,
            "cumulative_requests": None,
            "request_seconds": 45,
            "question_seconds": 120,
            "requests_per_question": 3,
            "max_output_tokens": 1536,
            "other_roles": 0,
        },
        "participant_limit": 3,
        "tasks_per_participant": 1,
        "task_source": (
            "New tasks using existing approved development cases; no holdout or first-use claim"
        ),
        "boundary": (
            "Explicit user-started diagnostic pilot; no correctness/efficiency/AI-gain scoring "
            "until independent reference review. Legacy preparation and stopped trials preserved."
        ),
        "cases": cases(engine, settings),
        "sources": sources(),
    }
    value["source_hash"] = canonical_hash(value["sources"])
    value["package_hash"] = canonical_hash(value)
    folder = ROOT / "packages" / value["package_hash"]
    folder.mkdir(parents=True, exist_ok=False)
    exclusive_json(folder / "package.json", value)
    return folder, value


def load(folder):
    folder = Path(folder).resolve()
    if not folder.is_relative_to((ROOT / "packages").resolve()):
        raise ValueError("Pilot package must stay in its own directory")
    value = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    if (
        folder.name != value["package_hash"]
        or canonical_hash({k: v for k, v in value.items() if k != "package_hash"})
        != value["package_hash"]
    ):
        raise ValueError("Pilot package integrity failed")
    return value


def check(folder, engine, settings):
    value = load(folder)
    if value["sources"] != sources() or value["cases"] != cases(engine, settings):
        raise ValueError("Pilot source or approved development revision changed")
    return value


def approve(folder):
    value = load(folder)
    intent = json.loads((ROOT / "authorization-intent.json").read_text(encoding="utf-8"))
    if (
        intent.get("requests") != ["启动真人使用", "额度不影响，随便用"]
        or intent.get("actor") != "human_user"
    ):
        raise ValueError("Explicit current user authorization missing")
    record = {
        "approved_by": "human_user",
        "authorization_id": AUTHORIZATION,
        "package_hash": value["package_hash"],
        "limits": value["limits"],
        "user_messages": intent["requests"],
        "authorized_at": clock(),
    }
    path = ROOT / "approval.json"
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if any(
            previous.get(key) != record[key]
            for key in (
                "approved_by",
                "authorization_id",
                "package_hash",
                "limits",
                "user_messages",
            )
        ):
            raise ValueError("Existing pilot approval differs; do not overwrite")
    else:
        exclusive_json(path, record)


def approval(folder):
    value = load(folder)
    record = json.loads((ROOT / "approval.json").read_text(encoding="utf-8"))
    if (
        record.get("approved_by") != "human_user"
        or record.get("authorization_id") != AUTHORIZATION
        or record.get("package_hash") != value["package_hash"]
        or record.get("limits") != value["limits"]
    ):
        raise ValueError("Pilot authorization mismatch")
    return value
