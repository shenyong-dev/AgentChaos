import json
from pathlib import Path

from app.agents.registry import ToolRegistry
from app.main import app
from app.schemas.contracts import TestCaseDraft
from app.schemas.contracts import TraceEvent as LegacyTraceEvent
from app.schemas.events import TraceEvent
from app.tools.booking import booking_tool_contracts

destination = Path(__file__).resolve().parents[2] / "docs" / "architecture"
destination.mkdir(parents=True, exist_ok=True)
for name, schema in (
    ("openapi.json", app.openapi()),
    ("test-case.schema.json", TestCaseDraft.model_json_schema()),
    ("trace-event.schema.json", TraceEvent.model_json_schema()),
    ("trace-event-v1.0.schema.json", LegacyTraceEvent.model_json_schema()),
    ("booking-tools.schema.json", booking_tool_contracts()),
    (
        "agent-tools.schema.json",
        {"version": "booking-registry-v1", "tools": ToolRegistry(None, None).schemas},
    ),
):
    (destination / name).write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {name}")
