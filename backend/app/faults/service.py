from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.executors.control import advisory
from app.models import FaultProfile
from app.schemas.faults import FaultSpec


def profile_spec(row):
    return FaultSpec.model_validate(
        {
            "profile_key": row.profile_key,
            "revision": row.revision,
            "fault_type": row.fault_type,
            "trigger_stage": row.trigger_stage,
            "target_tool": row.target_tool,
            "parameters": row.parameters_json,
            "seed": row.seed,
            "max_triggers": row.max_triggers,
            "enabled_for_test": row.enabled_for_test,
        }
    ).model_dump(mode="json")


def frozen_profile(session, identifier):
    row = session.get(FaultProfile, identifier)
    if row is None:
        raise AgentError("fault_profile_not_found", "故障配置不存在。", 404)
    try:
        spec = profile_spec(row)
    except ValueError:
        raise AgentError(
            "fault_profile_invalid", "故障修订不满足当前阶段/工具契约。", 409
        ) from None
    if not spec["enabled_for_test"]:
        raise AgentError("fault_profile_disabled", "此故障修订未启用测试。", 409)
    return {"id": row.id, "config": spec, "config_hash": canonical_hash(spec)}


class FaultService:
    def __init__(self, engine):
        self.engine = engine

    def create(self, spec):
        body = spec.model_dump(mode="json")
        with advisory(self.engine, f"fault:{spec.profile_key}:{spec.revision}") as connection:
            with Session(connection) as session, session.begin():
                row = session.scalar(
                    select(FaultProfile).where(
                        FaultProfile.profile_key == spec.profile_key,
                        FaultProfile.revision == spec.revision,
                    )
                )
                if row:
                    if profile_spec(row) != body:
                        raise AgentError(
                            "fault_revision_conflict", "同一故障修订不能覆盖，请创建新修订。", 409
                        )
                    return self.view(row)
                row = FaultProfile(
                    **{k: v for k, v in body.items() if k != "parameters"},
                    parameters_json=body["parameters"],
                )
                session.add(row)
                session.flush()
                return self.view(row)

    @staticmethod
    def view(row):
        spec = profile_spec(row)
        return {"id": row.id, **spec, "config_hash": canonical_hash(spec)}

    def read(self, identifier):
        with Session(self.engine) as session:
            row = session.get(FaultProfile, identifier)
            if row is None:
                raise AgentError("fault_profile_not_found", "故障配置不存在。", 404)
            return self.view(row)

    def page(self, after=0, limit=20):
        with Session(self.engine) as session:
            rows = list(
                session.scalars(
                    select(FaultProfile)
                    .where(FaultProfile.id > after)
                    .order_by(FaultProfile.id)
                    .limit(limit + 1)
                )
            )
            return {
                "items": [self.view(r) for r in rows[:limit]],
                "has_more": len(rows) > limit,
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }
