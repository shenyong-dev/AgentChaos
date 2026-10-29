"""Append-only calibration, blind semantic calls and explicit disagreement decisions."""

import json
import time
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.versions import canonical_hash
from app.cases.service import CaseService
from app.core.config import PROJECT_ROOT
from app.executors.control import advisory
from app.judging import evidence
from app.judging.budget import JudgeBudget
from app.judging.engine import agreement, interpret, manifest
from app.models import Artifact

DATASET = PROJECT_ROOT / "docs/fixtures/T12/samples.json"


def load_dataset():
    value = json.loads(DATASET.read_bytes())
    digest = value.pop("dataset_hash")
    if canonical_hash(value) != digest or len(value["samples"]) != 30:
        raise AgentError("judge_dataset_invalid", "校准样本清单或哈希不符。", 503)
    value["dataset_hash"] = digest
    for item in value["samples"]:
        if canonical_hash(item["payload"]) != item["sample_hash"]:
            raise AgentError("judge_dataset_invalid", "校准样本内容不匹配。", 503)
    return value


class JudgeService:
    authenticate = CaseService.authenticate
    role = staticmethod(CaseService.role)

    def __init__(self, engine, redis, settings, *, gateway_factory=None):
        self.engine, self.redis, self.settings = engine, redis, settings
        self.gateway_factory = gateway_factory

    def _rows(self, session, owner, batch):
        user = self.role(session, owner)
        request = session.scalar(
            select(Artifact).where(
                Artifact.kind == "judge_request",
                Artifact.run_id.is_(None),
                Artifact.metadata_json["generation_id"].as_string() == str(batch),
            )
        )
        if not request or user.role != "reviewer" and request.owner_id != owner:
            raise AgentError("judge_batch_not_found", "找不到当前身份可访问的评价批次。", 404)
        rows = list(
            session.scalars(
                select(Artifact)
                .where(
                    Artifact.owner_id == request.owner_id,
                    Artifact.kind.in_(
                        [
                            "judge_request",
                            "judge_call",
                            "judge_result",
                            "judge_label",
                            "judge_review",
                        ]
                    ),
                    Artifact.metadata_json["generation_id"].as_string() == str(batch),
                )
                .order_by(Artifact.id)
            )
        )
        return request, [(r, evidence.read(r)) for r in rows]

    def _append(self, owner, batch, kind, data, phase, **metadata):
        with Session(self.engine) as s, s.begin():
            row = evidence.append(
                s, self.settings, owner, batch, kind, data, {"phase": phase, **metadata}
            )
            return row.id

    def create(self, owner, request):
        batch = str(request.request_id)
        with advisory(self.engine, "judge-create:" + batch):
            with Session(self.engine) as session:
                self.role(session, owner, "operator")
                old = session.scalar(
                    select(Artifact).where(
                        Artifact.kind == "judge_request",
                        Artifact.metadata_json["generation_id"].as_string() == batch,
                    )
                )
            if old:
                return self.read(owner, batch)
            self._append(
                owner,
                batch,
                "judge_request",
                {
                    "dataset": load_dataset(),
                    "manifest": manifest(self.settings),
                    "created_at": datetime.now(UTC).isoformat(),
                    "mode": "offline_double" if self.gateway_factory else "real_provider",
                },
                "request",
            )
        return self.read(owner, batch)

    @staticmethod
    def _values(rows, phase):
        return {v["sample_id"]: v for r, v in rows if r.metadata_json.get("phase") == phase}

    @staticmethod
    def _request_replay(rows, request, owner, phase, sample=None):
        for row, value in rows:
            if row.metadata_json.get("request_id") == str(request.request_id):
                if (
                    row.metadata_json.get("request_hash")
                    != canonical_hash(request.model_dump(mode="json"))
                    or row.metadata_json.get("actor") != owner
                    or row.metadata_json.get("phase") != phase
                    or row.metadata_json.get("sample_id") != sample
                ):
                    raise AgentError("judge_request_conflict", "请求标识已用于另一内容。", 409)
                return value
        return None

    def label(self, reviewer, batch, sample_id, request):
        with advisory(self.engine, "judge-batch:" + str(batch)):
            with Session(self.engine) as s:
                user = self.role(s, reviewer, "reviewer")
                source, rows = self._rows(s, reviewer, batch)
                replay = self._request_replay(rows, request, reviewer, "label", sample_id)
                if replay:
                    return replay
                if any(r.metadata_json.get("phase") == "seal" for r, v in rows):
                    raise AgentError(
                        "judge_labels_sealed", "独立标注已封存，不能用裁判结果改写。", 409
                    )
                frozen = evidence.read(source)
            sample = next((x for x in frozen["dataset"]["samples"] if x["id"] == sample_id), None)
            if not sample or sample["sample_hash"] != request.sample_hash:
                raise AgentError("judge_sample_hash_conflict", "样本或内容哈希不匹配。", 409)
            value = {
                "sample_id": sample_id,
                "sample_hash": sample["sample_hash"],
                "support": request.support,
                "explanation": request.explanation,
                "note": request.note,
                "reviewer_id": reviewer,
                "reviewer_username": user.username,
                "reviewed_at": datetime.now(UTC).isoformat(),
                "source": "authenticated_human_input",
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
        with advisory(self.engine, "judge-batch:" + str(batch)):
            with Session(self.engine) as s:
                self.role(s, reviewer, "reviewer")
                source, rows = self._rows(s, reviewer, batch)
                old = next((v for r, v in rows if r.metadata_json.get("phase") == "seal"), None)
                if old:
                    return old
                frozen = evidence.read(source)
            labels = self._values(rows, "label")
            if request.dataset_hash != frozen["dataset"]["dataset_hash"] or set(labels) != {
                x["id"] for x in frozen["dataset"]["samples"]
            }:
                raise AgentError(
                    "judge_labels_incomplete", "需先真人标注全部30条并核对样本哈希。", 409
                )
            # One authenticated annotator in this first calibration, declared honestly.
            protocol = manifest(self.settings)
            if any(
                protocol[k] != frozen["manifest"][k]
                for k in (
                    "prompt_hash",
                    "schema_hash",
                    "threshold",
                    "model",
                    "thinking",
                    "max_output_tokens",
                )
            ):
                raise AgentError(
                    "judge_protocol_changed", "评价定义已变更，需新建批次重新声明。", 409
                )
            value = {
                "labels": labels,
                "labels_hash": canonical_hash(labels),
                "dataset_hash": request.dataset_hash,
                "sealed_by": reviewer,
                "sealed_at": datetime.now(UTC).isoformat(),
                "independent_from_judge": True,
                "annotators": sorted({x["reviewer_id"] for x in labels.values()}),
                "boundary": "Prior labels vs blind judge; no inter-human agreement claim.",
                "manifest": protocol,
            }
            self._append(
                source.owner_id,
                batch,
                "judge_label",
                value,
                "seal",
                request_id=str(request.request_id),
                request_hash=canonical_hash(request.model_dump(mode="json")),
                actor=reviewer,
            )
            return value

    def evaluate(self, owner, batch, sample_id, request):
        with advisory(self.engine, "judge-batch:" + str(batch)):
            with Session(self.engine) as s:
                self.role(s, owner, "operator")
                source, rows = self._rows(s, owner, batch)
                frozen = evidence.read(source)
            seal = next((v for r, v in rows if r.metadata_json.get("phase") == "seal"), None)
            if not seal:
                raise AgentError(
                    "judge_labels_not_sealed", "先完成人工独立标注并封存，再进行盲评。", 409
                )
            replay = self._request_replay(rows, request, owner, "result", sample_id)
            if replay:
                return replay
            old = self._values(rows, "result").get(sample_id)
            if old:
                return old
            if any(
                r.metadata_json.get("sample_id") == sample_id and r.kind == "judge_call"
                for r, v in rows
            ):
                raise AgentError(
                    "judge_call_incomplete", "已有调用尝试，不能自动再次付费；请复核原始记录。", 409
                )
            sample = next((x for x in frozen["dataset"]["samples"] if x["id"] == sample_id), None)
            if not sample:
                raise AgentError("judge_sample_not_found", "找不到校准样本。", 404)
            protocol = next(
                (v for r, v in rows if r.metadata_json.get("phase") == "protocol"), None
            )
            if protocol is None:
                protocol = manifest(self.settings)
                if any(
                    protocol[k] != seal["manifest"][k]
                    for k in (
                        "prompt_hash",
                        "schema_hash",
                        "threshold",
                        "model",
                        "thinking",
                        "max_output_tokens",
                    )
                ):
                    raise AgentError(
                        "judge_protocol_changed", "评价定义与人工标注前声明不同。", 409
                    )
                # Freeze final implementation before the first provider request. Labels stay sealed.
                self._append(source.owner_id, batch, "judge_result", protocol, "protocol")
            if protocol != manifest(self.settings):
                raise AgentError(
                    "judge_version_changed", "当前评价器不同于批次冻结版本，需新建独立批次。", 409
                )
            payload = sample["payload"]

            def record(stage, value):
                self._append(
                    source.owner_id, batch, "judge_call", value, stage, sample_id=sample_id
                )

            budget = JudgeBudget(self.redis, self.settings, str(batch), record)
            gateway = (
                self.gateway_factory(str(batch), sample_id)
                if self.gateway_factory
                else ModelGateway(
                    self.settings,
                    budget,
                    role="judge",
                    audit_directory=evidence.ROOT / "provider" / str(batch),
                )
            )
            # Only the exact allowlisted observed payload crosses the model boundary.
            # No labels/hashes, source type, rule verdict, reviewer or Agent prompt.
            messages = [
                SystemMessage(content=protocol["prompt"]),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
            self._append(
                source.owner_id,
                batch,
                "judge_call",
                {
                    "sample_hash": sample["sample_hash"],
                    "payload_hash": canonical_hash(payload),
                    "role": "judge",
                },
                "started",
                sample_id=sample_id,
            )
            try:
                message, raw = gateway.invoke(messages, [], deadline=time.monotonic() + 45)
                record("provider", raw)
                try:
                    result = interpret(json.loads(message.content), payload)
                except (ValueError, TypeError):
                    result = {
                        "status": "error",
                        "reason": "judge_invalid_json",
                        "needs_review": True,
                    }
                result["call_id"] = raw.get("call_id")
                result["usage"] = raw.get("usage")
            except AgentError as exc:
                if getattr(exc, "agent_record", None):
                    record("provider_error", exc.agent_record)
                result = {"status": "error", "reason": exc.code, "needs_review": True}
            result.update(
                {
                    "sample_id": sample_id,
                    "sample_hash": sample["sample_hash"],
                    "manifest_hash": canonical_hash(protocol),
                    "result_hash": None,
                    "role": "judge",
                    "mode": frozen["mode"],
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            )
            result["result_hash"] = canonical_hash(
                {k: v for k, v in result.items() if k != "result_hash"}
            )
            self._append(
                source.owner_id,
                batch,
                "judge_result",
                result,
                "result",
                sample_id=sample_id,
                request_id=str(request.request_id),
                actor=owner,
                request_hash=canonical_hash(request.model_dump(mode="json")),
            )
            return result

    def review(self, reviewer, batch, sample_id, request):
        with advisory(self.engine, "judge-batch:" + str(batch)):
            with Session(self.engine) as s:
                user = self.role(s, reviewer, "reviewer")
                source, rows = self._rows(s, reviewer, batch)
                replay = self._request_replay(rows, request, reviewer, "review", sample_id)
                if replay:
                    return replay
            original = self._values(rows, "label").get(sample_id)
            ai = self._values(rows, "result").get(sample_id)
            if (
                not original
                or not ai
                or canonical_hash(original) != request.original_label_hash
                or ai["result_hash"] != request.ai_result_hash
            ):
                raise AgentError(
                    "judge_review_hash_conflict", "复核所依据的独立标签或AI结果不匹配。", 409
                )
            if self._values(rows, "review").get(sample_id):
                raise AgentError("judge_already_reviewed", "已有复核决定，不能覆盖。", 409)
            value = {
                **request.model_dump(mode="json"),
                "sample_id": sample_id,
                "reviewer_id": reviewer,
                "reviewer_username": user.username,
                "reviewed_at": datetime.now(UTC).isoformat(),
                "source": "human_adjudication_not_calibration_relabeling",
            }
            self._append(
                source.owner_id,
                batch,
                "judge_review",
                value,
                "review",
                sample_id=sample_id,
                request_id=str(request.request_id),
                request_hash=canonical_hash(request.model_dump(mode="json")),
                actor=reviewer,
            )
            return value

    def read(self, owner, batch):
        with Session(self.engine) as s:
            source, rows = self._rows(s, owner, batch)
            frozen = evidence.read(source)
        labels = self._values(rows, "label")
        results = self._values(rows, "result")
        reviews = self._values(rows, "review")
        seal = next((v for r, v in rows if r.metadata_json.get("phase") == "seal"), None)
        stats = agreement(labels, results)
        stats["strata"] = {}
        for kind in {x["provenance"]["kind"] for x in frozen["dataset"]["samples"]}:
            ids = {x["id"] for x in frozen["dataset"]["samples"] if x["provenance"]["kind"] == kind}
            stats["strata"][kind] = agreement(
                {k: v for k, v in labels.items() if k in ids},
                {k: v for k, v in results.items() if k in ids},
            )
        return {
            "batch_id": str(batch),
            "owner_id": source.owner_id,
            "frozen": frozen,
            "seal": seal,
            "evaluation_protocol": next(
                (v for r, v in rows if r.metadata_json.get("phase") == "protocol"), None
            ),
            "labels": labels,
            "label_hashes": {k: canonical_hash(v) for k, v in labels.items()},
            "results": results,
            "reviews": reviews,
            "statistics": stats,
            "pending_reviews": [k for k in stats["needs_review"] if k not in reviews],
            "evidence": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "phase": r.metadata_json.get("phase"),
                    "sha256": r.sha256,
                }
                for r, v in rows
            ],
        }

    def page(self, owner):
        with Session(self.engine) as s:
            user = self.role(s, owner)
            query = select(Artifact).where(
                Artifact.kind == "judge_request", Artifact.run_id.is_(None)
            )
            if user.role != "reviewer":
                query = query.where(Artifact.owner_id == owner)
            rows = list(s.scalars(query.order_by(Artifact.id.desc()).limit(50)))
            return [
                {
                    "batch_id": r.metadata_json["generation_id"],
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
