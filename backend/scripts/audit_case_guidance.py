"""Read only the authorized development library; verify coverage and originals."""

import json
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT, Settings
from app.db.engine import create_database_engine
from app.executors.service import case_content
from app.models import TestCase
from app.workbench.service import WorkbenchService


def main():
    folder = PROJECT_ROOT / "docs/evidence/T17G"
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        service = WorkbenchService(engine, settings)
        items, cursor = [], 0
        while True:
            page = service.cases(1, cursor=cursor, limit=50)
            items += page["items"]
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        records, examples = [], {}
        for row in items:
            snapshot = service.case(1, row["id"])
            guide = snapshot["guidance"]
            with Session(engine) as session:
                original = case_content(session.get(TestCase, row["id"]))
            assert original["dataset_split"] == "dev"
            assert canonical_hash(original) == row["content_hash"] == guide["content_hash"]
            assert snapshot["content"] == original
            assert [t["user"] for t in guide["turns"]] == [t["user"] for t in original["turns"]]
            assert [c["parameters"] for c in guide["assertions"]] == original["expected"][
                "assertions"
            ]
            assert guide["revision"] == row["revision"]
            assert guide["guide_hash"] == row["guidance"]["guide_hash"]
            assert guide["status"] in ("available", "partial")
            records.append(
                {
                    "id": row["id"],
                    "case_key": row["case_key"],
                    "revision": row["revision"],
                    "category": row["category"],
                    "content_hash": row["content_hash"],
                    "guide_hash": guide["guide_hash"],
                    "status": guide["status"],
                    "missing": guide["missing"],
                    "knowledge_profile": row["knowledge_profile"],
                    "compatible": row["default_knowledge_compatible"],
                    "turns": len(guide["turns"]),
                    "assertions": len(guide["assertions"]),
                }
            )
            examples.setdefault(row["category"], guide)
        result = {
            "checked_at": datetime.now(UTC).isoformat(),
            "kind": "read-only coverage, not model evaluation or new human labels",
            "development_cases": len(records),
            "categories": dict(Counter(x["category"] for x in records)),
            "statuses": dict(Counter(x["status"] for x in records)),
            "knowledge_profiles": dict(Counter(x["knowledge_profile"] for x in records)),
            "default_compatible": sum(x["compatible"] for x in records),
            "bindings_and_originals_verified": True,
            "cases": records,
        }
        (folder / "coverage.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (folder / "six-development-examples.json").write_text(
            json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
