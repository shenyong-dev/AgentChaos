"""Authenticated shared and personal Agent registrations and case workflows."""

from uuid import uuid4

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.cases.service import CaseService
from app.core.roles import permits
from app.executors.control import advisory, database_now
from app.executors.service import case_content
from app.external import self_service
from app.external.adapter import ExternalAdapter
from app.external.cases import CaseFile, freeze, personal_case
from app.external.http_port import HttpPort
from app.external.registry import entries, entry_for
from app.external.runner import request
from app.external.schema import PREFIX, HttpEntry
from app.external.transport import Client
from app.external.web_config import entry_for_draft, profiles_for_ui, records, save_connection
from app.models import Target, TargetVersion, TestCase


class ExternalService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def actor(self, session, owner, *, personal=False):
        user = CaseService.role(session, owner)
        if user.role == "user" and personal and self_service.enabled(self.settings):
            return user
        return CaseService.role(session, owner, "operator")

    def definitions(self, owner):
        with Session(self.engine) as session:
            user = CaseService.role(session, owner)
        personal_access = permits(user.role, "operator") or user.role == "user"
        return {
            "role": user.role,
            "calls_enabled": self.settings.external_agent_calls_enabled,
            "self_service_enabled": personal_access and self_service.enabled(self.settings),
            "connection_profiles": profiles_for_ui(self.settings)
            if permits(user.role, "operator") and self.settings.external_agent_web_policy_path
            else [],
            "items": [
                {
                    "registered_id": entry.id,
                    "name": entry.card.name,
                    "revision": entry.card.revision,
                    "enabled": entry.enabled,
                    "billing": entry.card.billing,
                    "author_relation": entry.author_relation,
                    "personal": self_service.is_self(PREFIX + entry.id),
                    "protocol": entry.card.protocol,
                    "readback": isinstance(entry, HttpEntry) and bool(entry.http.readback_path),
                }
                for entry in (
                    *(entries(self.settings).values() if user.role != "user" else ()),
                    *(
                        self_service.owned(self.settings, owner)
                        if personal_access
                        else ()
                    ),
                )
            ],
        }

    def self_challenge(self, owner, address, protocol):
        with Session(self.engine) as session:
            self.actor(session, owner, personal=True)
        return self_service.challenge(self.settings, owner, address, protocol=protocol)

    def connect(self, owner, form):
        with Session(self.engine) as session:
            self.actor(session, owner, personal=True)
        saved, existing = self_service.candidate(self.settings, owner, form)
        kind = PREFIX + saved.entry.id
        if existing:
            manifest = ExternalAdapter(self.engine, self.settings, kind, owner=owner).manifest()
            inspection = {"config_hash": manifest["config_hash"], "name": saved.entry.card.name}
        else:
            try:
                inspection = self.probe(owner, saved.entry.id, personal=saved)
            except AgentError as e:
                if e.code != "external_result_unknown":
                    raise
                raise AgentError(
                    "external_self_connection_failed",
                    "连接失败。请核对服务是否在线、HTTPS 证书和访问令牌是否有效。",
                    503,
                ) from None
        return {
            **self.save_registration(
                owner, saved.entry.id, inspection["config_hash"], inspection, personal=saved
            ),
            "registered_id": saved.entry.id,
        }

    def inspect(self, owner, registered_id):
        return self.probe(owner, registered_id)

    def check_configuration(self, owner, draft):
        return self.probe(owner, draft.profile_id, draft=draft)

    def probe(self, owner, registered_id, *, draft=None, personal=None):
        with Session(self.engine) as session:
            self.actor(session, owner, personal=self_service.is_self(PREFIX + registered_id))
            server, database = session.execute(text("SELECT @@server_uuid,DATABASE()")).one()
        kind = PREFIX + registered_id
        entry = (
            personal.entry
            if personal is not None
            else entry_for_draft(self.settings, draft)
            if draft is not None
            else None
        )
        adapter = ExternalAdapter(self.engine, self.settings, kind, entry=entry, owner=owner)
        token = personal.access_token.get_secret_value() if personal is not None else None
        if isinstance(adapter.entry, HttpEntry):
            inspected = HttpPort(adapter.entry, self.settings, owner, access_token=token).verify()
            return {
                "registered_id": registered_id,
                "name": adapter.entry.card.name,
                "card": adapter.entry.card.model_dump(),
                "config_hash": adapter.manifest()["config_hash"],
                "connected": inspected,
                "configuration_valid": True,
                "billing_verified": False,
            }
        binding = {
            "tenant": canonical_hash([server, database, owner, kind]),
            "scope": "test:probe:" + uuid4().hex,
            "session_id": str(uuid4()),
        }
        card = Client(adapter.entry, self.settings, owner=owner, access_token=token).call(
            request(adapter.entry, binding, "capabilities", uuid4(), {})
        )
        if canonical_hash(card) != canonical_hash(adapter.entry.card.model_dump()):
            raise AgentError(
                "external_capability_changed", "能力卡不匹配，请核对名称、版本和服务费用。", 409
            )
        return {
            "registered_id": registered_id,
            "name": card["name"],
            "card": card,
            "config_hash": adapter.manifest()["config_hash"],
            "connected": True,
            "business_facts": "not_applicable",
            "billing_verified": False,
        }

    def register(self, owner, registered_id, expected_hash):
        inspected = self.inspect(owner, registered_id)
        return self.save_registration(owner, registered_id, expected_hash, inspected)

    def register_configuration(self, owner, draft, expected_hash):
        with Session(self.engine) as session:
            CaseService.role(session, owner, "operator")
        entry = entry_for_draft(self.settings, draft)
        record = records(self.settings).get(draft.profile_id)
        if (
            record
            and record.connection == draft
            and record.entry_hash == canonical_hash(entry.model_dump())
        ):
            # A connection is only saved after a verified capabilities receipt.
            # Its manifest is checked again under the registration lock.
            inspected = {"config_hash": expected_hash, "name": draft.name}
        else:
            inspected = self.check_configuration(owner, draft)
        return self.save_registration(
            owner, draft.profile_id, expected_hash, inspected, draft=draft
        )

    def save_registration(
        self, owner, registered_id, expected_hash, inspected, *, draft=None, personal=None
    ):
        if inspected["config_hash"] != expected_hash:
            raise AgentError("external_config_changed", "配置已变化，请重新检查连接与版本。", 409)
        kind = PREFIX + registered_id
        configuration_saved = False
        try:
            with (
                advisory(self.engine, "external-register"),
                Session(self.engine) as session,
                session.begin(),
            ):
                self.actor(session, owner, personal=self_service.is_self(kind))
                entry = (
                    personal.entry
                    if personal is not None
                    else entry_for_draft(self.settings, draft)
                    if draft is not None
                    else None
                )
                manifest = ExternalAdapter(
                    self.engine, self.settings, kind, entry=entry, owner=owner
                ).manifest()
                if manifest["config_hash"] != expected_hash:
                    raise AgentError("external_config_changed", "登记期间来源已变化。", 409)
                if draft is not None:
                    save_connection(self.settings, draft, manifest["external"]["entry_hash"])
                    configuration_saved = True
                if personal is not None:
                    self_service.save(self.settings, personal)
                    configuration_saved = True
                target = session.scalar(
                    select(Target)
                    .where(
                        Target.owner_id == owner,
                        Target.adapter_type == kind,
                        Target.archived_at.is_(None),
                    )
                    .with_for_update()
                )
                if target is None:
                    target = Target(
                        name=inspected["name"],
                        owner_id=owner,
                        adapter_type=kind,
                        description="外部 Agent 接口",
                        capabilities_json=manifest["capabilities"],
                    )
                    session.add(target)
                    session.flush()
                old = session.scalar(
                    select(TargetVersion).where(
                        TargetVersion.target_id == target.id,
                        TargetVersion.config_hash == expected_hash,
                    )
                )
                if old:
                    return {"target_id": target.id, "version_id": old.id, "created": False}
                number = (
                    session.scalar(
                        select(func.max(TargetVersion.version_no)).where(
                            TargetVersion.target_id == target.id
                        )
                    )
                    or 0
                )
                version = TargetVersion(
                    target_id=target.id,
                    version_no=number + 1,
                    model_config_json=manifest,
                    prompt_hash=manifest["prompt_hash"],
                    graph_revision=manifest["baseline"],
                    kb_manifest_json=manifest["retrieval"],
                    tool_revision="opentelemetry" if manifest["capabilities"]["tools"]
                    else "not_applicable",
                    adapter_revision=manifest["external"]["protocol"],
                    config_hash=expected_hash,
                )
                session.add(version)
                session.flush()
                return {"target_id": target.id, "version_id": version.id, "created": True}
        except SQLAlchemyError as e:
            if configuration_saved:
                raise AgentError(
                    "external_web_version_pending",
                    "服务配置已保存，版本登记暂未完成。请按原配置重试登记。",
                    503,
                ) from None
            if not isinstance(e, IntegrityError):
                raise
            raise AgentError(
                "external_registration_conflict", "登记记录发生并发变化，请重新核对。", 409
            ) from None

    def import_case(self, owner, body: CaseFile):
        if body.case.review_status != "draft" or body.case.dataset_split != "dev":
            raise AgentError(
                "external_case_draft_required", "导入只接受开发草稿，不能携带审核批准。", 400
            )
        personal = self_service.is_self(body.target_kind)
        source = f"external_import:{body.target_kind}:owner:{owner}"
        case = body.case.model_dump(mode="json")
        try:
            with Session(self.engine) as session, session.begin():
                self.actor(session, owner, personal=personal)
                entry = entry_for(self.settings, body.target_kind, owner=owner)
                if case["schema_version"] == "external-reliability-1" and not isinstance(
                    entry, HttpEntry
                ):
                    raise AgentError(
                        "external_evidence_unsupported", "该连接仅支持文本断言。", 409
                    )
                if personal and not session.scalar(
                    select(Target.id).where(
                        Target.owner_id == owner,
                        Target.adapter_type == body.target_kind,
                        Target.archived_at.is_(None),
                    )
                ):
                    raise AgentError("external_target_unavailable", "请先登记你的 Agent。", 409)
                row = TestCase(
                    case_key=case["case_key"],
                    revision=case["revision"],
                    family_key=case["family_key"],
                    category=case["category"],
                    dataset_split="dev",
                    source=source,
                    turns_json=case["turns"],
                    fixture_json={
                        "fixture": "external-dialogue-v1",
                        "adapter_type": body.target_kind,
                        "reference_clock": case["reference_clock"],
                    },
                    expected_json={
                        "schema_version": case["schema_version"],
                        "assertions": case["assertions"],
                    },
                    review_status="draft",
                    content_hash="0" * 64,
                )
                row.content_hash = canonical_hash(case_content(row))
                if personal:
                    freeze(row, case_content(row))
                session.add(row)
                session.flush()
                return {
                    "id": row.id,
                    "content_hash": row.content_hash,
                    "review_status": row.review_status,
                    "runnable": personal,
                }
        except IntegrityError:
            raise AgentError(
                "external_case_exists", "用例编号与修订已存在，请使用新的修订。", 409
            ) from None

    def cases(self, owner):
        with Session(self.engine) as session:
            user = CaseService.role(session, owner)
            query = (
                select(TestCase)
                .where(TestCase.source.startswith("external_import:"))
                .order_by(TestCase.id.desc())
            )
            own = TestCase.source.endswith(f":owner:{owner}")
            shared = ~TestCase.source.startswith("external_import:external_http:user-")
            if user.role == "admin":
                query = query.where(or_(shared, own))
            elif permits(user.role, "reviewer"):
                query = query.where(shared)
            else:
                query = query.where(own, ~shared) if user.role == "user" else query.where(own)
            return {
                "role": user.role,
                "items": [
                    {
                        "id": row.id,
                        "case_key": row.case_key,
                        "review_status": row.review_status,
                        "runnable": row.review_status == "approved" or personal_case(row, owner),
                        "content_hash": row.content_hash,
                        "content": case_content(row),
                    }
                    for row in session.scalars(query.limit(100))
                ],
            }

    def trace_setup(self, owner, registered_id):
        from app.external.otel import exporter_token

        kind = PREFIX + registered_id
        with Session(self.engine) as session:
            self.actor(session, owner, personal=self_service.is_self(kind))
            target = session.scalar(
                select(Target.id).where(
                    Target.owner_id == owner,
                    Target.adapter_type == kind,
                    Target.archived_at.is_(None),
                )
            )
        entry = entry_for(self.settings, kind, owner=owner)
        if not target or not isinstance(entry, HttpEntry):
            raise AgentError("external_trace_unavailable", "此对象不可配置外部轨迹。", 404)
        return {"path": "/api/v1/external-agents/otlp/v1/traces",
                "token": exporter_token(self.settings, owner, entry.id), "encoding": "http/json"}

    def review(self, owner, identifier, expected_hash, decision):
        with Session(self.engine) as session, session.begin():
            CaseService.role(session, owner, "reviewer")
            row = session.scalar(
                select(TestCase).where(TestCase.id == identifier).with_for_update()
            )
            if (
                row is None
                or not row.source.startswith("external_import:")
                or self_service.is_self(row.fixture_json.get("adapter_type"))
            ):
                raise AgentError("external_case_not_found", "找不到外部对话用例。", 404)
            if row.content_hash != expected_hash or row.content_hash != canonical_hash(
                case_content(row)
            ):
                raise AgentError("external_case_changed", "用例内容已变化，请重新阅读后审核。", 409)
            if row.review_status != "draft":
                if row.review_status == decision and row.reviewer_id == owner:
                    return {"id": row.id, "review_status": row.review_status}
                raise AgentError("external_case_reviewed", "用例已审核，不能覆盖原决定。", 409)
            freeze(row, case_content(row))
            row.review_status, row.reviewer_id, row.reviewed_at = (
                decision,
                owner,
                database_now(session),
            )
            return {"id": row.id, "review_status": decision}
