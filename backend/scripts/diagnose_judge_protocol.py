"""Read the original ninety T16 calls using the unchanged legacy response schema."""

import hashlib
import json
from collections import Counter

from pydantic import ValidationError

from app.core.config import PROJECT_ROOT
from app.schemas.judging import Judgment


def main():
    files = sorted((PROJECT_ROOT / "docs/evidence/T16/judge").glob("*/provider/*.json"))
    assert len(files) == 90
    counts, details = Counter(), []
    for path in files:
        body = path.read_bytes()
        call = json.loads(body)
        errors = []
        try:
            value = json.loads(call["response"]["content"])
            Judgment.model_validate(value)
        except json.JSONDecodeError:
            errors = [{"location": "$", "kind": "invalid_json"}]
        except ValidationError as exc:
            errors = [
                {"location": ".".join(map(str, e["loc"])), "kind": e["type"]} for e in exc.errors()
            ]
        for error in errors:
            counts[error["location"] + ":" + error["kind"]] += 1
        details.append(
            {
                "path": path.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": hashlib.sha256(body).hexdigest(),
                "errors": errors,
                "finish_reason": call["response"]["finish_reason"],
            }
        )
    report = {
        "original_calls": 90,
        "legacy_schema_invalid": sum(bool(x["errors"]) for x in details),
        "error_field_counts_overlap": dict(counts),
        "calls": details,
        "boundary": "Diagnostic only. No old labels, AI results or statistics rewritten.",
    }
    path = PROJECT_ROOT / "docs/evidence/T17B/legacy-diagnosis.json"
    assert not path.exists()
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "calls"}))


if __name__ == "__main__":
    main()
