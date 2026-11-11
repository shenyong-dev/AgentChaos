"""Owned source inspection and append-only registration; no model, seeds or deployment."""

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.refund import RefundAdapter
from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.cases.service import CaseService
from app.evidence.recorder import redact
from app.executors.service import RunService
from app.models import Target, TargetVersion

SUPPORTED = ("langgraph_booking", "reference_refund")
LABELS = {
    "baseline": "执行基线",
    "execution_policy": "工具执行策略",
    "provider": "模型服务商",
    "model_requested": "模型",
    "thinking": "思考模式",
    "max_output_tokens": "输出上限",
    "prompt_hash": "提示词",
    "sdk_versions": "SDK依赖",
    "retrieval": "知识与检索",
    "tools": "工具契约",
    "context": "上下文策略",
    "evaluator": "领域评价规则",
    "pricing": "计费估算规则",
    "capabilities": "对象能力",
    "provenance": "对象来源",
}


class VersionRegistrationService:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    @staticmethod
    def owned(session, owner, target_id, *, lock=False):
        CaseService.role(session, owner, "operator")
        query = select(Target).where(
            Target.id == target_id, Target.owner_id == owner, Target.archived_at.is_(None)
        )
        target = session.scalar(query.with_for_update() if lock else query)
        if target is None:
            raise AgentError("version_target_not_found", "当前账号下找不到可登记的测试对象。", 404)
        if target.adapter_type not in SUPPORTED:
            raise AgentError("version_adapter_unsupported", "该对象尚未提供版本登记适配器。", 409)
        return target

    def targets(self, owner):
        with Session(self.engine) as session:
            CaseService.role(session, owner, "operator")
            return {
                "items": [
                    {"id": t.id, "name": t.name, "adapter_type": t.adapter_type}
                    for t in session.scalars(
                        select(Target)
                        .where(
                            Target.owner_id == owner,
                            Target.archived_at.is_(None),
                            Target.adapter_type.in_(SUPPORTED),
                        )
                        .order_by(Target.id)
                    )
                ]
            }

    def current(self, adapter):
        return (
            RefundAdapter(self.engine, self.settings).manifest()
            if adapter == "reference_refund"
            else RunService(self.engine, self.settings).current_manifest()
        )

    @staticmethod
    def latest(session, target_id):
        return session.scalar(
            select(TargetVersion)
            .where(TargetVersion.target_id == target_id)
            .order_by(TargetVersion.version_no.desc())
            .limit(1)
        )

    @staticmethod
    def matching(session, target_id, digest):
        return session.scalar(
            select(TargetVersion)
            .where(TargetVersion.target_id == target_id, TargetVersion.config_hash == digest)
            .order_by(TargetVersion.id)
            .limit(1)
        )

    @staticmethod
    def brief(row):
        return (
            {"id": row.id, "version_no": row.version_no, "config_hash": row.config_hash}
            if row
            else None
        )

    def inspect(self, owner, target_id):
        with Session(self.engine) as session:
            target = self.owned(session, owner, target_id)
            current = self.current(target.adapter_type)
            base = self.latest(session, target_id)
            registered = self.matching(session, target_id, current["config_hash"])
            previous = base.model_config_json if base else {}
            changes = [
                {
                    "key": k,
                    "label": label,
                    "before": redact(previous.get(k), self.settings),
                    "after": redact(current.get(k), self.settings),
                }
                for k, label in LABELS.items()
                if previous.get(k) != current.get(k)
            ]
            other_keys = (set(previous) | set(current)) - set(LABELS) - {"sources", "config_hash"}
            old_other = {k: previous[k] for k in other_keys if k in previous}
            new_other = {k: current[k] for k in other_keys if k in current}
            if old_other != new_other:
                changes.append(
                    {
                        "key": "other",
                        "label": "其他配置指纹",
                        "before": canonical_hash(old_other),
                        "after": canonical_hash(new_other),
                    }
                )
            old_sources, new_sources = previous.get("sources", {}), current.get("sources", {})
            files = [
                {
                    "path": p,
                    "status": "added"
                    if p not in old_sources
                    else "removed"
                    if p not in new_sources
                    else "modified",
                    "before": old_sources.get(p),
                    "after": new_sources.get(p),
                }
                for p in sorted(set(old_sources) | set(new_sources))
                if old_sources.get(p) != new_sources.get(p)
            ]
            return {
                "target_id": target.id,
                "target": target.name,
                "adapter_type": target.adapter_type,
                "current_config_hash": current["config_hash"],
                "base_version": self.brief(base),
                "registered_version": self.brief(registered),
                "changes": changes,
                "source_changes": files,
                "source_count": len(new_sources),
            }

    def register(self, owner, request):
        # Share the existing booking CLI mutex. The target row also fences new API registrations.
        with self.engine.connect() as connection:
            acquired = connection.scalar(text("SELECT GET_LOCK('ac:t04:version-register',5)")) == 1
            connection.commit()
            if not acquired:
                raise AgentError("version_register_busy", "版本登记繁忙，请稍后重新核对。", 409)
            try:
                with Session(connection) as session, session.begin():
                    target = self.owned(session, owner, request.target_id, lock=True)
                    current = self.current(target.adapter_type)
                    if current["config_hash"] != request.expected_config_hash:
                        raise AgentError(
                            "version_source_changed", "源码或配置已变化，请重新核对后登记。", 409
                        )
                    existing = self.matching(session, target.id, current["config_hash"])
                    if existing:
                        return {**self.brief(existing), "target_id": target.id, "created": False}
                    base = self.latest(session, target.id)
                    if (base.id if base else None) != request.base_version_id:
                        raise AgentError(
                            "version_baseline_changed",
                            "对象已有新的登记记录，请重新核对变更。",
                            409,
                        )
                    if self.current(target.adapter_type)["config_hash"] != current["config_hash"]:
                        raise AgentError(
                            "version_source_changed",
                            "核对期间源码或配置发生变化，请重试核对。",
                            409,
                        )
                    refund = target.adapter_type == "reference_refund"
                    row = TargetVersion(
                        target_id=target.id,
                        version_no=(base.version_no if base else 0) + 1,
                        model_config_json=current,
                        prompt_hash=current["prompt_hash"],
                        graph_revision="reference-refund-v1"
                        if refund
                        else "T04-" + current["baseline"] + "-" + current["config_hash"][:12],
                        kb_manifest_json=current["retrieval"],
                        tool_revision="reference-refund-tools-v1"
                        if refund
                        else "booking-registry-v1",
                        adapter_revision="reference-refund-python-v1"
                        if refund
                        else "langgraph-booking-v1",
                        config_hash=current["config_hash"],
                    )
                    session.add(row)
                    session.flush()
                    return {**self.brief(row), "target_id": target.id, "created": True}
            except IntegrityError:
                raise AgentError(
                    "version_baseline_changed", "登记记录发生并发变化，请重新核对。", 409
                ) from None
            finally:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(text("SELECT RELEASE_LOCK('ac:t04:version-register')"))
                connection.commit()
