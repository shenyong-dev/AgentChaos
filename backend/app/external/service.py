"""Authenticated registration and case review; no arbitrary URL/approval import."""

from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.cases.service import CaseService
from app.executors.control import advisory, database_now
from app.executors.service import case_content
from app.external.adapter import ExternalAdapter
from app.external.cases import CaseFile, freeze
from app.external.registry import entries, entry_for
from app.external.runner import request
from app.external.schema import PREFIX
from app.external.transport import Client
from app.models import Target, TargetVersion, TestCase


class ExternalService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def definitions(self, owner):
        with Session(self.engine) as session:
            user = CaseService.role(session, owner)
        return {
            "role": user.role,
            "calls_enabled": self.settings.external_agent_calls_enabled,
            "items": [
                {
                    "registered_id": entry.id,
                    "name": entry.card.name,
                    "revision": entry.card.revision,
                    "enabled": entry.enabled,
                    "billing": entry.card.billing,
                    "author_relation": entry.author_relation,
                }
                for entry in entries(self.settings).values()
            ],
        }

    def inspect(self, owner, registered_id):
        with Session(self.engine) as session:
            CaseService.role(session, owner, "operator")
            server, database = session.execute(text("SELECT @@server_uuid,DATABASE()")).one()
        kind = PREFIX + registered_id
        adapter = ExternalAdapter(self.engine, self.settings, kind)
        binding = {
            "tenant": canonical_hash([server, database, owner, kind]),
            "scope": "test:probe:" + uuid4().hex,
            "session_id": str(uuid4()),
        }
        card = Client(adapter.entry, self.settings).call(
            request(adapter.entry, binding, "capabilities", uuid4(), {})
        )
        if canonical_hash(card) != canonical_hash(adapter.entry.card.model_dump()):
            raise AgentError(
                "external_capability_changed", "实际能力卡与登记文件不同，请由维护者核对。", 409
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
        if inspected["config_hash"] != expected_hash:
            raise AgentError("external_config_changed", "配置已变化，请重新检查连接与版本。", 409)
        kind = PREFIX + registered_id
        try:
            with (
                advisory(self.engine, "external-register"),
                Session(self.engine) as session,
                session.begin(),
            ):
                CaseService.role(session, owner, "operator")
                manifest = ExternalAdapter(self.engine, self.settings, kind).manifest()
                if manifest["config_hash"] != expected_hash:
                    raise AgentError("external_config_changed", "登记期间来源已变化。", 409)
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
                        description="外部对话接口",
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
                    graph_revision="external-dialogue-v1",
                    kb_manifest_json=manifest["retrieval"],
                    tool_revision="not_applicable",
                    adapter_revision="agentchaos-http-1",
                    config_hash=expected_hash,
                )
                session.add(version)
                session.flush()
                return {"target_id": target.id, "version_id": version.id, "created": True}
        except IntegrityError:
            raise AgentError(
                "external_registration_conflict", "登记记录发生并发变化，请重新核对。", 409
            ) from None

    def import_case(self, owner, body: CaseFile):
        if body.case.review_status != "draft" or body.case.dataset_split != "dev":
            raise AgentError(
                "external_case_draft_required", "导入只接受开发草稿，不能携带审核批准。", 400
            )
        entry_for(self.settings, body.target_kind)
        source = f"external_import:{body.target_kind}:owner:{owner}"
        case = body.case.model_dump(mode="json")
        try:
            with Session(self.engine) as session, session.begin():
                CaseService.role(session, owner, "operator")
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
                        "schema_version": "external-dialogue-1",
                        "assertions": case["assertions"],
                    },
                    review_status="draft",
                    content_hash="0" * 64,
                )
                row.content_hash = canonical_hash(case_content(row))
                session.add(row)
                session.flush()
                return {"id": row.id, "content_hash": row.content_hash, "review_status": "draft"}
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
            if user.role != "reviewer":
                query = query.where(TestCase.source.endswith(f":owner:{owner}"))
            return {
                "role": user.role,
                "items": [
                    {
                        "id": row.id,
                        "case_key": row.case_key,
                        "review_status": row.review_status,
                        "content_hash": row.content_hash,
                        "content": case_content(row),
                    }
                    for row in session.scalars(query.limit(100))
                ],
            }

    def review(self, owner, identifier, expected_hash, decision):
        with Session(self.engine) as session, session.begin():
            CaseService.role(session, owner, "reviewer")
            row = session.scalar(
                select(TestCase).where(TestCase.id == identifier).with_for_update()
            )
            if row is None or not row.source.startswith("external_import:"):
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
