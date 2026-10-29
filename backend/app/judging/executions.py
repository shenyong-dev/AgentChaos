"""Explicit semantic assistance on sealed executions, separate from objective verdicts."""

import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid5

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.versions import canonical_hash
from app.evaluation.service import EvaluationService, load_evidence
from app.executors.control import advisory
from app.judging import evidence
from app.judging.budget import JudgeBudget
from app.judging.engine import interpret, manifest
from app.judging.service import JudgeService
from app.models import Artifact, AssertionResult, KnowledgeDocument, TestExecution, TestRun


def observed_payload(bundle, execution, run):
    output = bundle.output or {}
    turns = execution.input_snapshot_json.get("dsl", {}).get("turns", [])
    indices = list(range(len(turns))) if len(turns) <= 6 else [0, 1, 2, len(turns) - 1]
    return {
        "dialogue": [{"turn": i + 1, "user": turns[i]["user"]} for i in indices],
        "dialogue_scope": "All turns"
        if len(turns) <= 6
        else "Selected first three and final turn; omitted turns are not evidence",
        "reply": output.get("assistant_text"),
        "evidence": [
            {"id": "sql", "kind": "sealed_business_snapshot", "content": bundle.after},
            {"id": "fields", "kind": "observed_state_not_SQL", "content": output.get("fields")},
            {
                "id": "retrieval",
                "kind": "observed_retrieval",
                "content": output.get("last_retrieval"),
            },
            {
                "id": "confirmation",
                "kind": "observed_state_not_permission",
                "content": {
                    k: output.get(k) for k in ("confirmation_state", "denials", "reference_clock")
                },
            },
            {
                "id": "public_rules",
                "kind": "frozen_knowledge_manifest_not_all_facts",
                "content": run.manifest_json["target"]["retrieval"],
            },
        ],
    }


class ExecutionJudgeService:
    def __init__(self, engine, redis, settings, *, gateway_factory=None):
        self.engine, self.redis, self.settings = engine, redis, settings
        self.gateway_factory = gateway_factory

    def evaluate(self, owner, identifier, request):
        calibration = JudgeService(self.engine, self.redis, self.settings).read(
            owner, request.calibration_batch_id
        )
        protocol = calibration.get("evaluation_protocol")
        if not self.gateway_factory and calibration["frozen"]["mode"] != "real_provider":
            raise AgentError(
                "judge_real_calibration_required", "离线控制不能作为真实裁判校准依据。", 409
            )
        if (
            not protocol
            or protocol != manifest(self.settings)
            or len(calibration["results"]) != 30
            or calibration["pending_reviews"]
            or not calibration["statistics"]["usable_assessments"]
        ):
            raise AgentError(
                "judge_calibration_required", "需先完成同版本30条盲评及人工分歧复核。", 409
            )
        with advisory(self.engine, "judge-execution:" + str(identifier)):
            with Session(self.engine) as session, session.begin():
                run, execution = EvaluationService.owned(session, owner, identifier, lock=True)
                bundle = load_evidence(session, run, execution)
                if not bundle.complete or not isinstance(
                    (bundle.output or {}).get("assistant_text"), str
                ):
                    raise AgentError(
                        "judge_evidence_incomplete",
                        "轨迹/回复/快照尚未完整封存，不发送裁判请求。",
                        409,
                    )
                payload = observed_payload(bundle, execution, run)
                documents = []
                for item in run.manifest_json["target"]["retrieval"]["documents"]:
                    document = session.get(KnowledgeDocument, item["id"])
                    if (
                        not document
                        or document.content_hash != item["content_hash"]
                        or hashlib.sha256(document.content.encode()).hexdigest()
                        != document.content_hash
                    ):
                        raise AgentError(
                            "judge_knowledge_unavailable", "冻结知识修订缺失或哈希不符。", 409
                        )
                    documents.append({**item, "content": document.content})
                payload["evidence"][-1] = {
                    "id": "public_rules",
                    "kind": "exact_frozen_documents_with_validity",
                    "content": documents,
                }
                run_id = run.id
                run_uuid = run.run_uuid
                execution_id = execution.id
                digest = canonical_hash({"payload": payload, "protocol": protocol})
                batch = str(uuid5(UUID(str(identifier)), digest))
                old = list(
                    session.scalars(
                        select(Artifact)
                        .where(
                            Artifact.run_id == run_id,
                            Artifact.metadata_json["generation_id"].as_string() == batch,
                        )
                        .order_by(Artifact.id)
                    )
                )
                result = next(
                    (
                        evidence.read(x)
                        for x in old
                        if x.metadata_json.get("phase") == "execution_result"
                    ),
                    None,
                )
                if result:
                    return result
                if old:
                    raise AgentError(
                        "judge_execution_incomplete",
                        "已有调用尝试，保留证据，不自动再次付费。",
                        409,
                    )
                frozen = {
                    "role": "judge",
                    "execution_id": str(identifier),
                    "payload": payload,
                    "protocol": protocol,
                    "calibration_batch_id": str(request.calibration_batch_id),
                    "calibration_labels_hash": calibration["seal"]["labels_hash"],
                    "input_hash": digest,
                    "mode": "offline_double" if self.gateway_factory else "real_provider",
                }
                evidence.append(
                    session,
                    self.settings,
                    None,
                    batch,
                    "judge_request",
                    frozen,
                    {"phase": "execution_request", "request_id": str(request.request_id)},
                    run_id=run_id,
                )

            def record(stage, value):
                with Session(self.engine) as s, s.begin():
                    evidence.append(
                        s,
                        self.settings,
                        None,
                        batch,
                        "judge_call",
                        value,
                        {"phase": stage},
                        run_id=run_id,
                    )

            budget = JudgeBudget(self.redis, self.settings, "execution:" + str(identifier), record)
            gateway = (
                self.gateway_factory(batch)
                if self.gateway_factory
                else ModelGateway(
                    self.settings,
                    budget,
                    role="judge",
                    audit_directory=evidence.ROOT / "provider" / batch,
                )
            )
            try:
                message, raw = gateway.invoke(
                    [
                        SystemMessage(content=protocol["prompt"]),
                        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
                    ],
                    [],
                    deadline=time.monotonic() + 45,
                )
                record("provider", raw)
                try:
                    result = interpret(json.loads(message.content), payload)
                except (ValueError, TypeError):
                    result = {
                        "status": "error",
                        "reason": "judge_invalid_json",
                        "needs_review": True,
                    }
                result["usage"] = raw.get("usage")
                result["call_id"] = raw.get("call_id")
            except AgentError as exc:
                if getattr(exc, "agent_record", None):
                    record("provider_error", exc.agent_record)
                result = {"status": "error", "reason": exc.code, "needs_review": True}
            result.update(
                {
                    "execution_id": str(identifier),
                    "run_id": run_uuid,
                    "input_hash": digest,
                    "protocol": protocol,
                    "calibration_batch_id": str(request.calibration_batch_id),
                    "mode": frozen["mode"],
                    "source": "ai_assistance_only",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            result["result_hash"] = canonical_hash(result)
            with Session(self.engine) as s, s.begin():
                for dimension in ("support", "explanation"):
                    assessment = result.get("assessment", {}).get(dimension)
                    refs = assessment["evidence_refs"] if assessment else []
                    references = {
                        "sql": f"snapshot:{identifier}:after",
                        "reply": f"snapshot:{identifier}:output",
                        "fields": f"snapshot:{identifier}:output",
                        "retrieval": f"snapshot:{identifier}:output",
                        "confirmation": f"snapshot:{identifier}:output",
                        "dialogue": f"execution:{identifier}:input",
                        "public_rules": f"manifest:{run_uuid}:target:retrieval",
                    }
                    s.add(
                        AssertionResult(
                            execution_id=execution_id,
                            assertion_key="semantic." + dimension,
                            metric_name="semantic_" + dimension,
                            evaluator_kind="ai",
                            evaluator_version="T12-" + digest[:40],
                            verdict=result["status"],
                            expected_json={"rubric": "T12", "threshold": protocol["threshold"]},
                            actual_json={
                                "value": assessment,
                                "input_hash": digest,
                                "evaluator": protocol,
                                "task_outcome": "not_evaluated",
                            },
                            evidence_refs_json=list(dict.fromkeys(references[x] for x in refs)),
                            reason=assessment["reason"] if assessment else result["reason"],
                            judge_usage_json=result.get("usage"),
                        )
                    )
                evidence.append(
                    s,
                    self.settings,
                    None,
                    batch,
                    "judge_result",
                    result,
                    {"phase": "execution_result"},
                    run_id=run_id,
                )
            return result

    def read(self, owner, identifier):
        with Session(self.engine) as s:
            account = JudgeService.role(s, owner)
            if account.role == "reviewer":
                execution = s.scalar(
                    select(TestExecution).where(TestExecution.execution_uuid == str(identifier))
                )
                if execution is None:
                    raise AgentError("execution_not_found", "找不到执行。", 404)
                run = s.get(TestRun, execution.run_id)
            else:
                run, execution = EvaluationService.owned(s, owner, identifier)
            rows = list(
                s.scalars(
                    select(Artifact)
                    .where(Artifact.run_id == run.id, Artifact.kind == "judge_result")
                    .order_by(Artifact.id)
                )
            )
            results = [
                evidence.read(x) for x in rows if x.metadata_json.get("phase") == "execution_result"
            ]
            results = [x for x in results if x["execution_id"] == str(identifier)]
            reviews = list(
                s.scalars(
                    select(Artifact)
                    .where(Artifact.run_id == run.id, Artifact.kind == "judge_review")
                    .order_by(Artifact.id)
                )
            )
            return {
                "execution_id": str(identifier),
                "items": results,
                "human_reviews": [
                    evidence.read(x)
                    for x in reviews
                    if x.metadata_json.get("execution_id") == str(identifier)
                ],
                "boundary": (
                    "AI assistance never changes rule verdict, task fulfilment, "
                    "bookings or the Agent context."
                ),
            }

    def review(self, reviewer, identifier, request):
        with advisory(self.engine, "judge-execution:" + str(identifier)):
            with Session(self.engine) as s:
                account = JudgeService.role(s, reviewer, "reviewer")
            data = self.read(reviewer, identifier)
            result = next(
                (x for x in data["items"] if x["result_hash"] == request.ai_result_hash), None
            )
            if not result:
                raise AgentError("judge_result_not_found", "复核结果哈希不匹配。", 409)
            old = next(
                (x for x in data["human_reviews"] if x["ai_result_hash"] == request.ai_result_hash),
                None,
            )
            body = request.model_dump(mode="json")
            if old:
                if old["request_hash"] == canonical_hash(body) and old["reviewer_id"] == reviewer:
                    return old
                raise AgentError("judge_already_reviewed", "此AI结果已有人工复核，不覆盖。", 409)
            value = {
                **body,
                "request_hash": canonical_hash(body),
                "reviewer_id": reviewer,
                "reviewer_username": account.username,
                "reviewed_at": datetime.now(UTC).isoformat(),
                "execution_id": str(identifier),
                "source": "human_adjudication",
                "boundary": "Does not modify original AI or objective rule verdicts.",
            }
            with Session(self.engine) as s, s.begin():
                execution = s.scalar(
                    select(TestExecution).where(TestExecution.execution_uuid == str(identifier))
                )
                batch = str(uuid5(UUID(str(identifier)), result["input_hash"]))
                for dimension in ("support", "explanation"):
                    label = body[dimension]
                    verdict = (
                        "inconclusive"
                        if label == "insufficient"
                        else "failed"
                        if label in ("contradicted", "unreasonable")
                        else "passed"
                    )
                    s.add(
                        AssertionResult(
                            execution_id=execution.id,
                            assertion_key="semantic." + dimension,
                            metric_name="semantic_" + dimension,
                            evaluator_kind="human",
                            evaluator_version="T12-human-" + canonical_hash(body)[:32],
                            verdict=verdict,
                            expected_json={"rubric": "T12 human adjudication"},
                            actual_json={
                                "value": label,
                                "input_hash": result["input_hash"],
                                "evaluator": {"version": "T12-human"},
                                "task_outcome": "not_evaluated",
                            },
                            evidence_refs_json=[f"judgment:{request.ai_result_hash}"],
                            reason=body["note"],
                            judge_usage_json=None,
                        )
                    )
                evidence.append(
                    s,
                    self.settings,
                    None,
                    batch,
                    "judge_review",
                    value,
                    {"phase": "execution_review", "execution_id": str(identifier)},
                    run_id=execution.run_id,
                )
            return value
