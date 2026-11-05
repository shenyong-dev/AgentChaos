import json
from pathlib import Path

from pydantic import TypeAdapter

from app.agents.registry import ToolRegistry
from app.main import app
from app.schemas.assertions import Assertion
from app.schemas.cases import Candidate, CandidateReview
from app.schemas.contracts import TestCaseDraft
from app.schemas.contracts import TraceEvent as LegacyTraceEvent
from app.schemas.copilot import CopilotAnswer, CopilotAsk, CopilotCreate
from app.schemas.datasets import DatasetReview
from app.schemas.events import TraceEvent
from app.schemas.faults import FaultSpec
from app.schemas.guidance import GuideAction, GuideContext
from app.schemas.interactive import InteractionCreate
from app.schemas.judging import (
    DisagreementReview,
    ExecutionJudgeReview,
    HumanLabel,
    Judgment,
    JudgmentV2,
)
from app.schemas.metamorphic import RelationCatalog
from app.schemas.readiness import ReadinessReport
from app.schemas.workflow import CommitPlan, RunPlan
from app.tools.booking import booking_tool_contracts

destination = Path(__file__).resolve().parents[2] / "docs" / "architecture"
destination.mkdir(parents=True, exist_ok=True)
for name, schema in (
    ("openapi.json", app.openapi()),
    ("guidance-context.schema.json", GuideContext.model_json_schema()),
    ("guidance-action.schema.json", GuideAction.model_json_schema()),
    ("copilot-create.schema.json", CopilotCreate.model_json_schema()),
    ("copilot-ask.schema.json", CopilotAsk.model_json_schema()),
    ("copilot-answer.schema.json", CopilotAnswer.model_json_schema()),
    ("run-plan.schema.json", RunPlan.model_json_schema()),
    ("run-commit.schema.json", CommitPlan.model_json_schema()),
    ("readiness-v1.schema.json", ReadinessReport.model_json_schema()),
    ("interactive-create.schema.json", InteractionCreate.model_json_schema()),
    ("test-case.schema.json", TestCaseDraft.model_json_schema()),
    ("trace-event.schema.json", TraceEvent.model_json_schema()),
    ("trace-event-v1.0.schema.json", LegacyTraceEvent.model_json_schema()),
    ("fault-profile.schema.json", FaultSpec.model_json_schema()),
    ("metamorphic.schema.json", RelationCatalog.model_json_schema()),
    ("case-candidate.schema.json", Candidate.model_json_schema()),
    ("case-review.schema.json", CandidateReview.model_json_schema()),
    ("semantic-judgment.schema.json", Judgment.model_json_schema()),
    ("semantic-judgment-v2.schema.json", JudgmentV2.model_json_schema()),
    ("semantic-human-label.schema.json", HumanLabel.model_json_schema()),
    ("semantic-review.schema.json", DisagreementReview.model_json_schema()),
    ("execution-semantic-review.schema.json", ExecutionJudgeReview.model_json_schema()),
    ("dataset-review.schema.json", DatasetReview.model_json_schema()),
    ("assertion.schema.json", TypeAdapter(Assertion).json_schema()),
    ("booking-tools.schema.json", booking_tool_contracts()),
    (
        "agent-tools.schema.json",
        {"version": "booking-registry-v1", "tools": ToolRegistry(None, None).schemas},
    ),
):
    path = destination / name
    newline = b"\r\n" if path.exists() and b"\r\n" in path.read_bytes() else b"\n"
    path.write_bytes(
        (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode().replace(b"\n", newline)
    )
    print(f"Exported {name}")
