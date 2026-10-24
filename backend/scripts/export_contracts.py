import json
from pathlib import Path

from app.main import app
from app.schemas.contracts import TestCaseDraft, TraceEvent

destination = Path(__file__).resolve().parents[2] / "docs" / "architecture"
destination.mkdir(parents=True, exist_ok=True)
for name, schema in (
    ("openapi.json", app.openapi()),
    ("test-case.schema.json", TestCaseDraft.model_json_schema()),
    ("trace-event.schema.json", TraceEvent.model_json_schema()),
):
    (destination / name).write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {name}")
