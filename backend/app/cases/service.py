"""Bounded generation and reviewer-only append-only decisions over real SQL."""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.gateway import ModelGateway
from app.agents.versions import canonical_hash
from app.cases import evidence
from app.cases.budget import GenerationBudget
from app.cases.validation import check, templates
from app.core.passwords import verify_password
from app.db.seeds import RULE_PATH
from app.evidence.recorder import redact
from app.executors.control import advisory
from app.executors.service import case_content
from app.models import Artifact, TestCase, User
from app.schemas.cases import Candidate


def manifest(settings, reference, profiles):
    prompt = (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")
    rules = RULE_PATH.read_text(encoding="utf-8")
    sources = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    sources["schema"] = canonical_hash(Candidate.model_json_schema())
    # Expected SQL counts are independently checked later, not supplied as labels to the model.
    risks = {
        p: {k: v for k, v in templates(reference)[p].items() if k != "expected_count"}
        for p in profiles
    }
    return {
        "version": "T11-v1",
        "role": "case_generator",
        "reference_clock": reference,
        "provider": "DeepSeek",
        "model": settings.casegen_model,
        "thinking": settings.casegen_thinking,
        "max_output_tokens": settings.casegen_max_output_tokens,
        "automatic_retries": 0,
        "limits": {
            "calls": 2,
            "repairs": 1,
            "tokens": 200_000,
            "microusd": 100_000,
            "seconds": 90,
            "candidates": len(profiles),
        },
        "prompt": prompt,
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "public_rules": rules,
        "rules_hash": hashlib.sha256(rules.encode()).hexdigest(),
        "source_ref": "knowledge/booking-rules-v1.md",
        "sources": sources,
        "candidate_schema": Candidate.model_json_schema(),
        "risks": risks,
        "input_boundary": (
            "Only allowlisted public rule file, authored risk templates and schema. "
            "No dataset queries in prompt construction."
        ),
    }


class CaseService:
    def __init__(self, engine, redis, settings, *, gateway_factory=None, clock=None):
        self.engine, self.redis, self.settings = engine, redis, settings
        self.gateway_factory = gateway_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    def authenticate(self, username, password):
        with Session(self.engine) as session:
            user = session.scalar(select(User).where(User.username == username))
            if not user or not user.enabled or not verify_password(password, user.password_hash):
                raise AgentError("case_authentication_failed", "账号或密码不正确。", 401)
            return user.id

    @staticmethod
    def role(session, identifier, required=None):
        user = session.get(User, identifier)
        if not user or not user.enabled or required and user.role != required:
            raise AgentError("case_role_required", "当前账号没有此操作角色。", 403)
        return user

    def _request(self, session, owner, batch):
        user = self.role(session, owner)
        row = session.scalar(
            select(Artifact).where(
                Artifact.kind == "casegen_request",
                Artifact.metadata_json["generation_id"].as_string() == str(batch),
            )
        )
        if row is None or user.role != "reviewer" and row.owner_id != owner:
            raise AgentError("generation_not_found", "找不到当前身份可访问的候选生成批次。", 404)
        return row

    def _append(self, owner, batch, kind, value, metadata):
        with Session(self.engine) as session, session.begin():
            row = evidence.append(session, self.settings, owner, batch, kind, value, metadata)
            return row.id

    @staticmethod
    def _duplicates(session, validation, *, exclude_case_key=None, before_candidate_id=None):
        # Deliberately dev-only. No holdout text or answers are loaded even for deduplication.
        found = []
        query = select(TestCase).where(
            TestCase.dataset_split == "dev", TestCase.review_status != "rejected"
        )
        if before_candidate_id is not None:
            query = query.where(
                or_(
                    TestCase.review_status == "approved",
                    TestCase.fixture_json["generation"]["candidate_artifact_id"].as_integer()
                    < before_candidate_id,
                )
            )
        for row in session.scalars(query):
            if exclude_case_key and row.case_key == exclude_case_key:
                continue
            meta = row.fixture_json.get("generation", {})
            if any(
                meta.get(k) == validation[k] for k in ("text_fingerprint", "semantic_fingerprint")
            ):
                found.append(row.id)
        return found

    def _store_candidate(self, owner, batch, index, raw, frozen):
        raw = redact(raw, self.settings)
        candidate, validation = None, None
        try:
            candidate = Candidate.model_validate(raw)
            validation = check(candidate, frozen["reference_clock"], frozen["risks"])
            errors = []
        except ValidationError as exc:
            errors = [{"location": list(x["loc"]), "type": x["type"]} for x in exc.errors()]
        with Session(self.engine) as session, session.begin():
            duplicates = self._duplicates(session, validation) if validation else []
            status = (
                "schema_error"
                if errors
                else "duplicate"
                if duplicates
                else "semantic_error"
                if validation["issues"]
                else "valid"
            )
            data = {
                "raw": raw,
                "candidate_hash": canonical_hash(raw),
                "schema_errors": errors,
                "validation": validation,
                "duplicates": duplicates,
                "status": status,
                "index": index,
                "mode": "offline_double" if self.gateway_factory else "real_provider",
            }
            artifact = evidence.append(
                session,
                self.settings,
                owner,
                batch,
                "casegen_result",
                data,
                {"phase": "candidate", "status": status, "index": index},
            )
            if candidate:
                row = self._new_case(
                    candidate,
                    frozen,
                    owner,
                    batch,
                    artifact.id,
                    validation,
                    case_key="CG-" + uuid4().hex.upper(),
                    revision=1,
                )
                session.add(row)
            return artifact.id

    @staticmethod
    def _new_case(candidate, frozen, owner, batch, artifact_id, validation, *, case_key, revision):
        value = candidate.model_dump(mode="json")
        family = (
            "generated-booking-" + templates(frozen["reference_clock"])["normal"]["initial"]["date"]
        )
        row = TestCase(
            case_key=case_key,
            revision=revision,
            family_key=family,
            category="robustness"
            if candidate.profile == "capacity"
            else "memory_and_tool"
            if candidate.profile == "correction"
            else "normal",
            dataset_split="dev",
            source="case_generator:T11:" + str(batch),
            turns_json=value["turns"],
            fixture_json={
                "fixture": "booking-default-v1",
                "reference_clock": frozen["reference_clock"],
                "generation": {
                    "owner_id": owner,
                    "batch_id": str(batch),
                    "candidate_artifact_id": artifact_id,
                    "profile": candidate.profile,
                    "candidate": value,
                    "manifest_hash": canonical_hash(frozen),
                    **{k: validation[k] for k in ("text_fingerprint", "semantic_fingerprint")},
                },
            },
            expected_json={
                "schema_version": "1.1",
                "assertions": value["assertions"],
                "task_goal": value["task_goal"],
            },
            review_status="draft",
            content_hash="0" * 64,
        )
        row.content_hash = canonical_hash(case_content(row))
        return row

    def generate(self, owner, request):
        batch = str(request.request_id)
        request_hash = canonical_hash(request.model_dump(mode="json"))
        with advisory(self.engine, "casegen-owner:" + str(owner)):
            with advisory(self.engine, "casegen:" + batch):
                with Session(self.engine) as session:
                    self.role(session, owner, "operator")
                    existing = session.scalar(
                        select(Artifact).where(
                            Artifact.kind == "casegen_request",
                            Artifact.metadata_json["generation_id"].as_string() == batch,
                        )
                    )
                    if existing:
                        if existing.owner_id != owner:
                            raise AgentError("generation_not_found", "找不到该批次。", 404)
                        if existing.metadata_json["request_hash"] != request_hash:
                            raise AgentError(
                                "generation_request_conflict", "相同请求标识的配置不同。", 409
                            )
                        return self.read(
                            owner, batch
                        )  # No automatic regeneration after interruption.
                reference = self.clock().isoformat()
                frozen = manifest(self.settings, reference, request.profiles)
                self._append(
                    owner,
                    batch,
                    "casegen_request",
                    frozen,
                    {
                        "request_hash": request_hash,
                        "phase": "request",
                        "mode": "offline_double" if self.gateway_factory else "real_provider",
                    },
                )

                def record(stage, value):
                    return self._append(owner, batch, "casegen_call", value, {"phase": stage})

                budget = GenerationBudget(self.redis, self.settings, batch, record)
                if self.gateway_factory:
                    gateway = self.gateway_factory(batch)
                else:
                    gateway = ModelGateway(
                        self.settings,
                        budget,
                        role="case_generator",
                        audit_directory=evidence.ROOT / "provider" / batch,
                    )
                messages = [
                    SystemMessage(content=frozen["prompt"]),
                    HumanMessage(
                        content=json.dumps(
                            {
                                k: frozen[k]
                                for k in (
                                    "public_rules",
                                    "reference_clock",
                                    "candidate_schema",
                                    "risks",
                                )
                            },
                            ensure_ascii=False,
                        )
                    ),
                ]
                deadline = time.monotonic() + 90
                attempts, candidate_ids, error = [], [], None
                for attempt in range(2):
                    content = ""
                    try:
                        message, raw_call = gateway.invoke(messages, [], deadline=deadline)
                        self._append(
                            owner,
                            batch,
                            "casegen_call",
                            raw_call,
                            {"phase": "provider", "attempt": attempt + 1},
                        )
                        content = message.content
                        if not isinstance(content, str) or not content.strip():
                            raise ValueError("empty_output")
                        decoded = json.loads(content)
                        if (
                            not isinstance(decoded, dict)
                            or set(decoded) != {"candidates"}
                            or not isinstance(decoded["candidates"], list)
                            or not 1 <= len(decoded["candidates"]) <= len(request.profiles)
                        ):
                            raise ValueError("batch_shape")
                        for index, raw in enumerate(decoded["candidates"], 1):
                            candidate_ids.append(
                                self._store_candidate(owner, batch, index, raw, frozen)
                            )
                        attempts.append(
                            {
                                "attempt": attempt + 1,
                                "outcome": "parsed",
                                "received": len(candidate_ids),
                            }
                        )
                        returned_profiles = {
                            r.get("profile") for r in decoded["candidates"] if isinstance(r, dict)
                        }
                        missing_profiles = sorted(set(request.profiles) - returned_profiles)
                        attempts[-1]["missing_profiles"] = missing_profiles
                        error = "missing_requested_profiles" if missing_profiles else None
                        break
                    except (ValueError, AgentError) as exc:
                        code = (
                            exc.code
                            if isinstance(exc, AgentError)
                            else "invalid_json"
                            if isinstance(exc, json.JSONDecodeError)
                            else str(exc)
                        )
                        if getattr(exc, "agent_record", None):
                            self._append(
                                owner,
                                batch,
                                "casegen_call",
                                exc.agent_record,
                                {"phase": "provider_error", "attempt": attempt + 1},
                            )
                        attempts.append({"attempt": attempt + 1, "outcome": code})
                        error = code
                        if attempt or code not in (
                            "invalid_json",
                            "empty_output",
                            "batch_shape",
                            "model_output_truncated",
                            "model_empty_stream",
                            "model_empty_response",
                        ):
                            break
                        messages += [
                            AIMessage(content=content or "[empty or truncated output]"),
                            HumanMessage(
                                content="仅修复输出格式。重新完整输出同一批候选的JSON对象，不能省略原模板或添加审核/身份字段；这是最后一次修复。"
                            ),
                        ]
                self._append(
                    owner,
                    batch,
                    "casegen_result",
                    {
                        "candidate_ids": candidate_ids,
                        "attempts": attempts,
                        "error": error,
                        "ledger": budget.snapshot(),
                        "requested": len(request.profiles),
                    },
                    {"phase": "batch_result"},
                )
        return self.read(owner, batch)

    def read(self, owner, batch):
        with Session(self.engine) as session:
            request = self._request(session, owner, batch)
            frozen = evidence.read(request)
            rows = list(
                session.scalars(
                    select(Artifact)
                    .where(
                        Artifact.metadata_json["generation_id"].as_string() == str(batch),
                        Artifact.owner_id == request.owner_id,
                    )
                    .order_by(Artifact.id)
                )
            )
            final = next((r for r in rows if r.metadata_json.get("phase") == "batch_result"), None)
            candidates = []
            for row in rows:
                if row.metadata_json.get("phase") != "candidate":
                    continue
                value = evidence.read(row)
                revisions = list(
                    session.scalars(
                        select(TestCase)
                        .where(
                            TestCase.fixture_json["generation"][
                                "candidate_artifact_id"
                            ].as_integer()
                            == row.id
                        )
                        .order_by(TestCase.revision)
                    )
                )
                decisions = [
                    r
                    for r in rows
                    if r.kind == "case_review" and r.metadata_json.get("candidate_id") == row.id
                ]
                # Recheck is a derived view. Never rewrite the original ingestion result,
                # which remains the denominator for initial model-quality metrics.
                current = {"status": "schema_error", "validation": None, "duplicates": []}
                try:
                    checked = check(
                        Candidate.model_validate(value["raw"]),
                        frozen["reference_clock"],
                        frozen["risks"],
                    )
                    duplicate_ids = self._duplicates(
                        session,
                        checked,
                        exclude_case_key=revisions[0].case_key if revisions else None,
                        before_candidate_id=row.id,
                    )
                    current = {
                        "status": "duplicate"
                        if duplicate_ids
                        else "semantic_error"
                        if checked["issues"]
                        else "valid",
                        "validation": checked,
                        "duplicates": duplicate_ids,
                    }
                except ValidationError:
                    pass
                candidates.append(
                    {
                        "id": row.id,
                        **value,
                        "recheck": {
                            **current,
                            "checker_hash": hashlib.sha256(
                                (Path(__file__).parent / "validation.py").read_bytes()
                            ).hexdigest(),
                        },
                        "case_revisions": [
                            {
                                "id": c.id,
                                "revision": c.revision,
                                "status": c.review_status,
                                "content_hash": c.content_hash,
                                "candidate": c.fixture_json["generation"]["candidate"],
                            }
                            for c in revisions
                        ],
                        "review": evidence.read(decisions[-1]) if decisions else None,
                    }
                )
            result = evidence.read(final) if final else None
            counts = {
                s: sum(c["status"] == s for c in candidates)
                for s in ("valid", "semantic_error", "schema_error", "duplicate")
            }
            return {
                "generation_id": str(batch),
                "owner_id": request.owner_id,
                "status": "error"
                if result and result["error"]
                else "completed"
                if final
                else "incomplete",
                "mode": request.metadata_json["mode"],
                "manifest": frozen,
                "manifest_hash": canonical_hash(frozen),
                "candidates": candidates,
                "result": result,
                "metrics": {
                    "received": len(candidates),
                    "valid_before_review": counts["valid"],
                    "valid_rate": counts["valid"] / len(candidates) if candidates else None,
                    "counts": counts,
                    "reviewed": sum(bool(c["review"]) for c in candidates),
                    "edited": sum(
                        bool(c["review"] and c["review"]["decision"] == "edit") for c in candidates
                    ),
                },
                "evidence": [
                    {
                        "id": r.id,
                        "kind": r.kind,
                        "phase": r.metadata_json.get("phase"),
                        "sha256": r.sha256,
                    }
                    for r in rows
                ],
            }

    def page(self, owner, after=0, limit=20):
        with Session(self.engine) as session:
            user = self.role(session, owner)
            query = select(Artifact).where(Artifact.kind == "casegen_request", Artifact.id > after)
            if user.role != "reviewer":
                query = query.where(Artifact.owner_id == owner)
            rows = list(session.scalars(query.order_by(Artifact.id).limit(limit + 1)))
            return {
                "items": [
                    {
                        "id": r.id,
                        "generation_id": r.metadata_json["generation_id"],
                        "owner_id": r.owner_id,
                        "created_at": r.created_at.isoformat(),
                        "mode": r.metadata_json["mode"],
                    }
                    for r in rows[:limit]
                ],
                "has_more": len(rows) > limit,
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }

    def artifact(self, owner, batch, artifact_id):
        with Session(self.engine) as session:
            request = self._request(session, owner, batch)
            row = session.get(Artifact, artifact_id)
            if (
                not row
                or row.owner_id != request.owner_id
                or row.metadata_json.get("generation_id") != str(batch)
            ):
                raise AgentError("generation_evidence_not_found", "找不到该批次证据。", 404)
            return evidence.read(row)

    def review(self, reviewer, candidate_id, request):
        with Session(self.engine) as session:
            user = self.role(session, reviewer, "reviewer")
            username = user.username
            raw = session.get(Artifact, candidate_id)
            if not raw or raw.metadata_json.get("phase") != "candidate":
                raise AgentError("candidate_not_found", "找不到候选。", 404)
            batch, owner = raw.metadata_json["generation_id"], raw.owner_id
            original = evidence.read(raw)
            frozen = evidence.read(self._request(session, reviewer, batch))
        if request.expected_candidate_hash != original["candidate_hash"]:
            raise AgentError("candidate_hash_conflict", "候选内容已不匹配，请重新读取。", 409)
        body_hash = canonical_hash(request.model_dump(mode="json"))
        family = (
            "generated-booking-" + templates(frozen["reference_clock"])["normal"]["initial"]["date"]
        )
        with advisory(self.engine, "case-family:" + family) as connection:
            with Session(connection) as session, session.begin():
                reviews = list(
                    session.scalars(select(Artifact).where(Artifact.kind == "case_review"))
                )
                for row in reviews:
                    if row.metadata_json.get("request_id") == str(request.request_id):
                        if (
                            row.metadata_json.get("request_hash") != body_hash
                            or row.metadata_json.get("reviewer_id") != reviewer
                            or row.metadata_json.get("candidate_id") != candidate_id
                        ):
                            raise AgentError(
                                "review_request_conflict", "审核请求标识已用于另一内容。", 409
                            )
                        return evidence.read(row)
                    if row.metadata_json.get("candidate_id") == candidate_id:
                        raise AgentError(
                            "candidate_already_reviewed", "该候选已有不可变审核决定。", 409
                        )
                new_id, changes = None, []
                if request.decision != "reject":
                    try:
                        candidate = request.edited or Candidate.model_validate(original["raw"])
                    except ValueError:
                        raise AgentError(
                            "candidate_invalid", "格式错误的候选需明确修改后才能通过。", 409
                        ) from None
                    if (
                        not isinstance(original["raw"], dict)
                        or original["raw"].get("profile") not in frozen["risks"]
                    ):
                        raise AgentError(
                            "candidate_invalid", "原始候选缺少有效风险类型，请拒绝后重新生成。", 409
                        )
                    if candidate.profile != original["raw"].get("profile"):
                        raise AgentError(
                            "candidate_profile_changed", "更换风险模板需重新生成候选。", 409
                        )
                    validation = check(candidate, frozen["reference_clock"], frozen["risks"])
                    prior = session.scalar(
                        select(TestCase)
                        .where(
                            TestCase.fixture_json["generation"][
                                "candidate_artifact_id"
                            ].as_integer()
                            == candidate_id
                        )
                        .order_by(TestCase.revision.desc())
                        .with_for_update()
                    )
                    duplicates = self._duplicates(
                        session,
                        validation,
                        exclude_case_key=prior.case_key if prior else None,
                        before_candidate_id=candidate_id,
                    )
                    if validation["issues"] or duplicates:
                        raise AgentError(
                            "candidate_review_blocked",
                            "候选仍有明确预期错误或重复，不能通过。",
                            409,
                        )
                    if session.scalar(
                        select(TestCase.id)
                        .where(
                            TestCase.family_key == family,
                            TestCase.review_status == "approved",
                            TestCase.dataset_split != "dev",
                        )
                        .limit(1)
                    ):
                        raise AgentError("case_family_split", "同家族已属于其他分区。", 409)
                    value = candidate.model_dump(mode="json")
                    changes = [k for k in value if value[k] != original["raw"].get(k)]
                    row = self._new_case(
                        candidate,
                        frozen,
                        owner,
                        batch,
                        candidate_id,
                        validation,
                        case_key=prior.case_key if prior else "CG-" + uuid4().hex.upper(),
                        revision=prior.revision + 1 if prior else 1,
                    )
                    row.review_status, row.reviewer_id, row.reviewed_at = (
                        "approved",
                        reviewer,
                        self.clock(),
                    )
                    session.add(row)
                    session.flush()
                    new_id = row.id
                else:
                    for draft in session.scalars(
                        select(TestCase)
                        .where(
                            TestCase.fixture_json["generation"][
                                "candidate_artifact_id"
                            ].as_integer()
                            == candidate_id,
                            TestCase.review_status == "draft",
                        )
                        .with_for_update()
                    ):
                        draft.review_status = "rejected"
                        draft.reviewer_id, draft.reviewed_at = reviewer, self.clock()
                decision = {
                    "candidate_id": candidate_id,
                    "candidate_hash": original["candidate_hash"],
                    "generation_id": batch,
                    "reviewer_id": reviewer,
                    "reviewer_username": username,
                    "reviewed_at": self.clock().isoformat(),
                    "decision": request.decision,
                    "note": request.note,
                    "changed_fields": changes,
                    "approved_case_revision_id": new_id,
                    "approved_content_hash": row.content_hash if new_id else None,
                    "request_id": str(request.request_id),
                }
                evidence.append(
                    session,
                    self.settings,
                    owner,
                    batch,
                    "case_review",
                    decision,
                    {
                        "candidate_id": candidate_id,
                        "request_id": str(request.request_id),
                        "request_hash": body_hash,
                        "reviewer_id": reviewer,
                    },
                )
                return decision
