"""Opaque, origin-bound Redis sessions. No password or raw session ID is stored."""

import hashlib
import json
import secrets
import time
from functools import lru_cache
from urllib.parse import urlsplit

from fastapi import Request
from redis import Redis, RedisError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.core.config import get_settings
from app.core.passwords import verify_password
from app.models import User

# A single Redis operation checks expiry and touches TTL. A concurrent logout cannot
# resurrect a deleted key. Stream checks deliberately do not count as user activity.
READ = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {0} end
local s = cjson.decode(raw)
local now = tonumber(ARGV[1])
if now >= s.absolute or now >= s.idle then
  redis.call('DEL', KEYS[1]); return {0}
end
if s.origin ~= ARGV[2] then return {-1} end
if ARGV[3] == '1' and s.csrf ~= ARGV[4] then return {-2} end
if ARGV[5] == '1' then
  s.idle = math.min(s.absolute, now + tonumber(ARGV[6]))
  raw = cjson.encode(s)
  redis.call('SET', KEYS[1], raw, 'EX', math.max(1, math.ceil(s.idle - now)))
end
return {1, raw}
"""
ATTEMPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], 300) end
return n
"""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def account_stamp(user):
    return digest(json.dumps([user.id, user.password_hash, user.role, user.enabled]))


def request_origin(request: Request, *, write=False):
    # Host must be preserved by the same-origin reverse proxy. Forwarded host is
    # never trusted here; deployment must terminate HTTPS with trusted ASGI scheme.
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    client = request.headers.get("x-ac-origin", "")
    origin = request.headers.get("origin")
    if client != expected or origin is not None and origin != expected:
        raise AgentError("session_origin_forbidden", "此会话仅可在原登录入口使用。", 403)
    if write and origin != expected:
        raise AgentError("session_origin_forbidden", "请求来源无法确认，请从原页面操作。", 403)
    if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        raise AgentError("session_origin_forbidden", "请在同一入口操作。", 403)
    parsed = urlsplit(expected)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AgentError("session_origin_forbidden", "登录入口无效。", 403)
    return expected


class SessionService:
    def __init__(self, engine, redis, settings, *, clock=time.time):
        self.engine, self.redis, self.settings, self.clock = engine, redis, settings, clock
        binding = digest(
            json.dumps(
                [
                    settings.mysql_host,
                    settings.mysql_port,
                    settings.mysql_database,
                    settings.executor_namespace,
                ]
            )
        )[:20]
        self.prefix = "ac:browser-session:v1:" + binding + ":"
        self.cookie_name = "ac_session_" + binding

    def key(self, token):
        return self.prefix + digest(token)

    def cookie(self, request):
        return request.cookies.get(self.cookie_name, "")

    def _redis(self, method, *args, **kwargs):
        try:
            return getattr(self.redis, method)(*args, **kwargs)
        except RedisError as exc:
            raise AgentError(
                "session_unavailable", "登录会话服务暂不可用，请稍后重试。", 503
            ) from exc

    def login(self, request, username, password):
        origin = request_origin(request, write=True)
        if self.settings.app_env == "production" and request.url.scheme != "https":
            raise AgentError("session_https_required", "生产登录须使用HTTPS。", 403)
        # Bounded counters contain hashes, not account names or credentials.
        for scope, limit in (
            ("ip:" + (request.client.host if request.client else "unknown"), 40),
            ("account:" + username.casefold(), 12),
        ):
            if self._redis("eval", ATTEMPT, 1, self.prefix + "attempt:" + digest(scope)) > limit:
                raise AgentError("session_rate_limited", "登录尝试过多，请5分钟后再试。", 429)
        try:
            with Session(self.engine) as db:
                user = db.scalar(select(User).where(User.username == username))
                if (
                    not user
                    or not user.enabled
                    or not verify_password(password, user.password_hash)
                ):
                    raise AgentError("session_credentials_invalid", "账号或密码不正确。", 401)
                now = int(self.clock())
                record = {
                    "user_id": user.id,
                    "username": user.username,
                    "role": user.role,
                    "stamp": account_stamp(user),
                    "csrf": secrets.token_urlsafe(32),
                    "origin": origin,
                    "absolute": now + self.settings.session_absolute_seconds,
                    "idle": now + self.settings.session_idle_seconds,
                }
        except DBAPIError as exc:
            raise AgentError("session_unavailable", "账号服务暂不可用。", 503) from exc
        old = self.cookie(request)
        if old:
            # Only revoke an existing cookie belonging to this exact browser origin.
            raw = self._redis("get", self.key(old))
            if raw and json.loads(raw)["origin"] == origin:
                self._redis("delete", self.key(old))
        token = secrets.token_urlsafe(32)
        self._redis(
            "set", self.key(token), json.dumps(record), ex=self.settings.session_idle_seconds
        )
        return token, record

    def resolve(self, request, *, touch=True):
        token = self.cookie(request)
        if len(token) != 43:
            raise AgentError("session_expired", "会话已过期或尚未登录，请重新登录。", 401)
        write = request.method not in ("GET", "HEAD", "OPTIONS")
        origin = request_origin(request, write=write)
        result = self._redis(
            "eval",
            READ,
            1,
            self.key(token),
            self.clock(),
            origin,
            "1" if write else "0",
            request.headers.get("x-csrf-token", ""),
            "1" if touch else "0",
            self.settings.session_idle_seconds,
        )
        if result[0] == -1:
            raise AgentError("session_origin_forbidden", "请在原登录入口操作。", 403)
        if result[0] == -2:
            raise AgentError("session_csrf_forbidden", "操作校验已失效，请刷新后重试。", 403)
        if result[0] != 1:
            raise AgentError("session_expired", "会话已过期，请重新登录。", 401)
        record = json.loads(result[1])
        try:
            with Session(self.engine) as db:
                user = db.get(User, record["user_id"])
                valid = (
                    user
                    and user.enabled
                    and secrets.compare_digest(account_stamp(user), record["stamp"])
                )
        except DBAPIError as exc:
            raise AgentError("session_unavailable", "账号服务暂不可用。", 503) from exc
        if not valid:
            self._redis("delete", self.key(token))
            raise AgentError("session_expired", "账号权限已变更，请重新登录。", 401)
        request.state.browser_session = True
        return record

    def logout(self, request):
        self.resolve(request, touch=False)
        self._redis("delete", self.key(self.cookie(request)))


@lru_cache
def get_session_service():
    from app.api.booking import get_booking_engine

    settings = get_settings()
    return SessionService(
        get_booking_engine(),
        Redis.from_url(settings.redis_url, socket_timeout=3, socket_connect_timeout=3),
        settings,
    )


def browser_identity(request, *, operator=False):
    record = get_session_service().resolve(
        request, touch=request.headers.get("x-ac-activity") == "1"
    )
    if operator and record["role"] != "operator":
        raise AgentError(
            "session_role_forbidden", "当前审核账号无权执行操作员功能，请切换账号。", 403
        )
    return record["user_id"]
