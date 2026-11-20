"""Platform management over existing account, session and run records."""

import json
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.auth.service import account_stamp
from app.core.model_access import global_enabled
from app.core.passwords import hash_password
from app.executors.budget import authorization_limit
from app.executors.control import advisory
from app.interactive.budget import authorized
from app.models import ModelCallPolicy, Target, TargetVersion, TestRun, User

log = logging.getLogger("agentchaos.admin")
SESSION_ID = re.compile(r"[a-f0-9]{64}")


def account(user):
    return dict(
        id=user.id,
        username=user.username,
        role=user.role,
        enabled=user.enabled,
        model_calls_enabled=user.model_calls_enabled,
    )


def require_current_admin(db, actor):
    user = db.get(User, actor, with_for_update=True)
    if not user or not user.enabled or user.role != "admin":
        raise AgentError("admin_required", "此操作仅限平台管理员。", 403)


class AdminService:
    def __init__(self, auth):
        self.auth = auth
        self.engine = auth.engine

    def accounts(self, cursor, query):
        statement = select(User).where(User.id > cursor).order_by(User.id).limit(51)
        if query:
            statement = statement.where(User.username.contains(query, autoescape=True))
        with Session(self.engine) as db:
            rows = list(db.scalars(statement))
            return dict(
                items=[account(row) for row in rows[:50]],
                next_cursor=rows[49].id if len(rows) > 50 else None,
            )

    def summary(self):
        with Session(self.engine) as db:
            counts = dict(db.execute(select(User.role, func.count()).group_by(User.role)).all())
            runs = db.scalar(select(func.count()).select_from(TestRun))
            targets = db.scalar(select(func.count()).select_from(Target))
            model_calls = global_enabled(db)
        settings = self.auth.settings
        has_key = bool(settings.deepseek_api_key.get_secret_value())
        return dict(
            accounts=sum(counts.values()),
            roles=counts,
            targets=targets,
            runs=runs,
            model_calls_enabled=model_calls,
            interactive_configured=has_key and authorized(settings),
            batch_configured=has_key
            and settings.executor_allow_paid_model_calls
            and bool(authorization_limit(settings)),
        )

    def change_model_calls(self, actor, enabled, target=None):
        with (
            advisory(self.engine, "admin-account-change") as connection,
            Session(connection) as db,
            db.begin(),
        ):
            require_current_admin(db, actor)
            if target is None:
                policy = db.get(ModelCallPolicy, 1, with_for_update=True)
                if policy is None:
                    db.add(ModelCallPolicy(id=1, enabled=enabled))
                else:
                    policy.enabled = enabled
            else:
                row = db.get(User, target, with_for_update=True)
                if row is None:
                    raise AgentError("account_not_found", "账号不存在。", 404)
                row.model_calls_enabled = enabled
        log.info("real model access changed actor=%s target=%s enabled=%s", actor, target, enabled)
        return {"model_calls_enabled": enabled}

    def runs(self, cursor):
        with Session(self.engine) as db:
            statement = (
                select(TestRun, User.username, Target.name)
                .join(User, User.id == TestRun.created_by)
                .join(TargetVersion, TargetVersion.id == TestRun.target_version_id)
                .join(Target, Target.id == TargetVersion.target_id)
                .order_by(TestRun.id.desc())
            )
            if cursor:
                statement = statement.where(TestRun.id < cursor)
            rows = list(db.execute(statement.limit(21)))
            return dict(
                items=[
                    dict(
                        run_id=row.run_uuid,
                        owner=username,
                        target=name,
                        status=row.status,
                        started_at=row.started_at,
                        finished_at=row.finished_at,
                    )
                    for row, username, name in rows[:20]
                ],
                next_cursor=rows[19][0].id if len(rows) > 20 else None,
            )

    def create(self, actor, body):
        password_hash = hash_password(body.password.get_secret_value())
        try:
            with (
                advisory(self.engine, "admin-account-change") as connection,
                Session(connection) as db,
                db.begin(),
            ):
                require_current_admin(db, actor)
                row = User(
                    username=body.username,
                    role=body.role,
                    enabled=True,
                    password_hash=password_hash,
                )
                db.add(row)
                db.flush()
                result = account(row)
        except IntegrityError:
            raise AgentError("account_exists", "该账号已存在。", 409) from None
        log.info("account created actor=%s target=%s role=%s", actor, result["id"], body.role)
        return result

    def change(self, actor, target, body):
        if actor == target:
            raise AgentError("account_self_protected", "请由其他管理员修改当前登录账号。", 409)
        password_hash = hash_password(body.password.get_secret_value()) if body.password else None
        with advisory(self.engine, "admin-account-change") as connection:
            with Session(connection) as db, db.begin():
                require_current_admin(db, actor)
                row = db.get(User, target, with_for_update=True)
                if row is None:
                    raise AgentError("account_not_found", "账号不存在。", 404)
                changed = False
                for name in ("enabled", "role"):
                    value = getattr(body, name)
                    if value is not None and value != getattr(row, name):
                        setattr(row, name, value)
                        changed = True
                if password_hash:
                    row.password_hash = password_hash
                    changed = True
                if changed:
                    row.auth_epoch += 1
                db.flush()
                result = account(row)
        if changed:
            log.info(
                "account updated actor=%s target=%s role=%s enabled=%s password_reset=%s",
                actor,
                target,
                result["role"],
                result["enabled"],
                bool(password_hash),
            )
        return result

    def sessions(self, request, cursor):
        prefix = self.auth.prefix
        current = self.auth.key(self.auth.cookie(request))
        keys = {}
        next_cursor = cursor
        for _ in range(6):
            next_cursor, batch = self.auth._redis(
                "scan", cursor=next_cursor, match=prefix + "?" * 64, count=200
            )
            for raw in batch:
                key = raw.decode() if isinstance(raw, bytes) else raw
                if SESSION_ID.fullmatch(key[len(prefix) :]):
                    keys[key] = None
            if not next_cursor or len(keys) >= 50:
                break
        valid = list(keys)
        values = self.auth._redis("mget", valid) if valid else []
        found = [(key, json.loads(raw)) for key, raw in zip(valid, values, strict=True) if raw]
        identities = {record["user_id"] for _, record in found}
        with Session(self.engine) as db:
            users = {row.id: row for row in db.scalars(select(User).where(User.id.in_(identities)))}
            now = self.auth.clock()
            sessions = []
            for key, record in found:
                user = users.get(record["user_id"])
                if (
                    not user
                    or not user.enabled
                    or record["stamp"] != account_stamp(user)
                    or now >= min(record["idle"], record["absolute"])
                ):
                    continue
                sessions.append(
                    dict(
                        id=key[len(prefix) :],
                        user_id=user.id,
                        username=user.username,
                        role=user.role,
                        created_at=record.get("created_at"),
                        idle_expires_at=record["idle"],
                        absolute_expires_at=record["absolute"],
                        current=key == current,
                    )
                )
        return dict(items=sessions, next_cursor=str(next_cursor) if next_cursor else None)

    def revoke(self, request, actor, identifier):
        key = self.auth.prefix + identifier
        if key == self.auth.key(self.auth.cookie(request)):
            raise AgentError("account_self_protected", "当前会话请使用退出登录。", 409)
        removed = bool(self.auth._redis("delete", key))
        log.info("login revoked actor=%s removed=%s", actor, removed)
        return dict(revoked=removed)
