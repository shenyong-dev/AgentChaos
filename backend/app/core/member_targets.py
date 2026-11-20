"""Owned targets and the platform's shared project references."""

from sqlalchemy import and_, or_, select

from app.models import Target, User

REFERENCE_NAMES = {
    "langgraph_booking": "场地预约实验对象",
    "reference_refund": "订单售后与模拟退款 · 项目参考",
}


def reference_target_filter():
    seed_owner = select(User.id).where(User.username == "demo_operator").scalar_subquery()
    return and_(
        Target.owner_id == seed_owner,
        or_(
            *(
                and_(Target.adapter_type == adapter, Target.name == name)
                for adapter, name in REFERENCE_NAMES.items()
            )
        ),
    )


def accessible_target_filter(user):
    owned = Target.owner_id == user.id
    if user.role == "user":
        return or_(reference_target_filter(), and_(owned, personal_target_filter()))
    return or_(owned, reference_target_filter()) if user.role in {"admin", "operator"} else owned


def personal_target_filter():
    from app.external.self_service import SELF_PREFIX

    return Target.adapter_type.like(SELF_PREFIX.replace("_", "!_") + "_" * 32, escape="!")
