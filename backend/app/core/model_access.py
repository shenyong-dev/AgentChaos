"""Persistent access controls for real Agent calls, independent of provider budgets."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.models import ModelCallPolicy, User


def global_enabled(db):
    policy = db.get(ModelCallPolicy, 1)
    return bool(policy and policy.enabled)


def denied_call(db, owner):
    result = db.execute(
        select(User.enabled, User.model_calls_enabled, ModelCallPolicy.enabled)
        .outerjoin(ModelCallPolicy, ModelCallPolicy.id == 1)
        .where(User.id == owner)
    ).one_or_none()
    if result is None or not result[0]:
        return "model_account_unavailable", "当前账号不可用。"
    if not result[2]:
        return "model_calls_globally_disabled", "平台已关闭真实模型调用，请联系管理员。"
    if not result[1]:
        return "model_calls_account_disabled", "当前账号未开启真实模型调用，请联系管理员。"
    return None


def require_model_call(db, owner):
    if error := denied_call(db, owner):
        raise AgentError(*error, 403)


def guard_model_call(engine, owner):
    with Session(engine) as db:
        require_model_call(db, owner)
