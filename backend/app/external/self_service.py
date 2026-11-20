"""Account-owned HTTPS agents. Verify DNS control before sending any credential."""

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
from time import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from dns.exception import DNSException
from dns.resolver import resolve
from pydantic import Field, SecretStr
from redis import Redis
from redis.exceptions import RedisError

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.external.schema import (
    PREFIX,
    PROTOCOL,
    Card,
    ConnectProtocol,
    Entry,
    HttpCard,
    HttpEntry,
    HttpProfile,
    Strict,
)

SELF_PREFIX = PREFIX + "user-"
KEY_ID = re.compile(r"user-[0-9a-f]{32}")
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}")
PATH = re.compile(r"(?:/[a-zA-Z0-9_-]+)*/agentchaos/v1")
TOKEN = re.compile(r"[!-~]{16,512}")
TTL = 3600
RATE = """
for i=1,#KEYS do
    if tonumber(redis.call('GET',KEYS[i]) or '0')>=tonumber(ARGV[i]) then
        return 0
    end
end
for i=1,#KEYS do
    if redis.call('INCR',KEYS[i])==1 then redis.call('EXPIRE',KEYS[i],60) end
end
return 1
"""


class Address(Strict):
    endpoint: str = Field(min_length=1, max_length=420)
    protocol: ConnectProtocol = PROTOCOL


class Connect(Address):
    name: str = Field(min_length=1, max_length=100)
    revision: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    access_token: SecretStr
    cost_upper_microusd: int = Field(default=0, ge=0, le=100_000)
    http: HttpProfile | None = None


class Saved(Strict):
    owner: int
    entry: Entry | HttpEntry
    access_token: SecretStr


def is_self(kind):
    return (
        isinstance(kind, str)
        and kind.startswith(PREFIX)
        and KEY_ID.fullmatch(kind[len(PREFIX) :]) is not None
    )


def _key(settings):
    return settings.external_agent_self_service_key.get_secret_value()


def _base(settings):
    p = Path(settings.external_agent_web_policy_path)
    scope = canonical_hash(
        [settings.mysql_database, settings.executor_namespace, settings.cloud_deployment_id]
    )[:16]
    return p.parent / ("self-service-" + scope)


def enabled(settings):
    p = Path(settings.external_agent_web_policy_path)
    try:
        return bool(
            settings.external_agent_calls_enabled
            and p.is_absolute()
            and p.parent.is_dir()
            and Fernet(_key(settings).encode("ascii"))
            and len(base64.urlsafe_b64decode(_key(settings))) == 32
        )
    except (ValueError, TypeError):
        return False


def require_enabled(settings):
    if not enabled(settings):
        raise AgentError("external_self_disabled", "此环境尚未启用自助接入。", 403)


def endpoint(raw, protocol=PROTOCOL):
    clean = raw.strip()
    try:
        parsed = urlsplit(clean)
        host = parsed.hostname or ""
        if (
            not clean.isascii()
            or parsed.scheme != "https"
            or len(host) > 220
            or not DOMAIN.fullmatch(host)
            or parsed.netloc.lower() not in (host, host + ":443")
            or not (
                PATH.fullmatch(parsed.path)
                if protocol == PROTOCOL
                else re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?", parsed.path)
            )
            or not re.fullmatch(r"[A-Za-z0-9:/._-]+", clean)
            or host.endswith((".local", ".localhost", ".internal", ".home", ".lan", ".test"))
        ):
            raise ValueError
        path = parsed.path
        if protocol == "dify":
            if path.rstrip("/").endswith("/v1"):
                path = path.rstrip("/") + "/chat-messages"
            if not path.endswith("/chat-messages"):
                raise ValueError
        return f"https://{host}{path}", host
    except ValueError:
        raise AgentError(
            "external_invalid_endpoint",
            "填写公网 HTTPS 地址，路径须以 /agentchaos/v1 结尾。"
            if protocol == PROTOCOL
            else "请填写公网 HTTPS 接口地址。Dify 可填 /v1 或 /chat-messages 接口。",
            400,
        ) from None


def signature(settings, purpose, *parts):
    raw = canonical_hash([purpose, _base(settings).name, *parts]).encode()
    return hmac.new(base64.urlsafe_b64decode(_key(settings)), raw, hashlib.sha256).hexdigest()


def managed_dify(url, protocol):
    return protocol == "dify" and url == "https://api.dify.ai/v1/chat-messages"


def challenge(settings, owner, address, *, slot=None, protocol=PROTOCOL):
    require_enabled(settings)
    url, host = endpoint(address, protocol)
    if managed_dify(url, protocol):
        return {"managed": True}
    # A stable proof for the current hour, accepted for one additional hour.
    hour = int(time() // TTL) if slot is None else slot
    value = "agentchaos-" + signature(settings, "dns-ownership-v1", owner, url, hour)
    return {"name": "_agentchaos." + host, "value": value}


def verify_txt(settings, owner, url, protocol):
    now = int(time() // TTL)
    proof = challenge(settings, owner, url, slot=now, protocol=protocol)
    accepted = {
        proof["value"].encode(),
        challenge(settings, owner, url, slot=now - 1, protocol=protocol)["value"].encode(),
    }
    try:
        answers = resolve(proof["name"] + ".", "TXT", lifetime=3.0, search=False)
        if not any(b"".join(r.strings) in accepted for r in answers):
            raise ValueError
    except (DNSException, ValueError):
        raise AgentError(
            "external_dns_unverified", "尚未找到你的域名验证记录。请核对 TXT 记录并稍后重试。", 409
        ) from None


def pin_ip(settings, owner, url, protocol=PROTOCOL):
    url, host = endpoint(url, protocol)
    if not managed_dify(url, protocol):
        verify_txt(settings, owner, url, protocol)
    try:
        answers = resolve(host + ".", "A", lifetime=3.0, search=False)
        addresses = sorted({public_ip(r.address) for r in answers})
        if not addresses or len(addresses) > 16:
            raise ValueError
        return str(addresses[0])
    except (DNSException, ValueError):
        raise AgentError(
            "external_dns_address", "域名须解析到有效的公网 IPv4 地址。", 409
        ) from None


def public_ip(value):
    ip = ipaddress.IPv4Address(value)
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_private:
        raise ValueError("Public unicast IPv4 required")
    return ip


def limited(settings, owner):
    namespace = settings.executor_namespace + ":" + _base(settings).name + ":connect"
    try:
        with Redis.from_url(
            settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        ) as redis:
            ok = redis.eval(RATE, 2, f"{namespace}:{owner}", namespace + ":all", 4, 16)
    except RedisError:
        raise AgentError(
            "external_connect_unavailable", "暂时无法验证连接，请稍后再试。", 503
        ) from None
    if ok != 1:
        raise AgentError("external_connect_rate", "连接检查过于频繁，请稍后再试。", 429)


def candidate(settings, owner, form):
    require_enabled(settings)
    url, _ = endpoint(form.endpoint, form.protocol)
    token = form.access_token.get_secret_value()
    name = form.name.strip()
    if (
        not TOKEN.fullmatch(token)
        or not name
        or token in name
        or token in url
        or token in form.revision
    ):
        raise AgentError(
            "external_invalid_credentials", "请填写 Agent 名称和有效的服务访问令牌。", 400
        )
    profile = http_profile(form, url)
    if profile and (
        token in profile.model_dump_json()
        or profile.readback_path
        and urlsplit(url).path.rstrip("/") == profile.readback_path.rstrip("/")
    ):
        raise AgentError("external_mapping_invalid", "请核对字段映射与只读接口路径。", 400)
    identity = "user-" + signature(settings, "agent-id-v1", owner, url, form.revision)[:32]
    current = read(settings, owner, identity)
    if current:
        entry = current.entry
        if (
            not hmac.compare_digest(current.access_token.get_secret_value(), token)
            or entry.endpoint != url
            or entry.card.name != name
            or entry.cost_upper_microusd != form.cost_upper_microusd
            or entry.card.protocol != form.protocol
            or getattr(entry, "http", None) != profile
        ):
            raise AgentError(
                "external_self_version_exists",
                "该地址和版本已有登记。更改配置请填写新的版本号。",
                409,
            )
        return current, True
    limited(settings, owner)
    within_quota(settings, owner)
    ip = (
        pin_ip(settings, owner, url)
        if form.protocol == PROTOCOL
        else pin_ip(settings, owner, url, form.protocol)
    )
    billing = "external_account" if form.cost_upper_microusd else "free"
    fields = dict(
        id=identity,
        endpoint=url,
        pinned_ip=ip,
        timeout_seconds=30 if form.protocol == "dify" else 12,
        credential_env="AGENTCHAOS_EXTERNAL_SELF",
        enabled=True,
        author_relation="external_developer",
        cost_upper_microusd=form.cost_upper_microusd,
    )
    if profile is None:
        entry = Entry(
            **fields,
            card=Card(
                protocol=PROTOCOL,
                name=name,
                revision=form.revision,
                billing=billing,
                side_effects=False,
                request_lookup=True,
                cancellation=True,
                cancel_before_execute=True,
            ),
        )
    else:
        entry = HttpEntry(
            **fields,
            http=profile,
            card=HttpCard(
                protocol=form.protocol, name=name, revision=form.revision, billing=billing
            ),
        )
    return Saved(owner=owner, entry=entry, access_token=form.access_token), False


def http_profile(form, url):
    if form.protocol == PROTOCOL:
        if form.http is not None:
            raise AgentError("external_mapping_unsupported", "请选择支持字段映射的接入方式。", 400)
        return None
    profile = form.http or HttpProfile()
    if form.protocol == "http-json":
        return profile
    if managed_dify(url, form.protocol) and (profile.readback_path or profile.sandbox_tool_fault):
        raise AgentError("external_readback_origin", "托管 Dify 地址仅用于 Chat API。", 400)
    if {"query", "conversation_id", "user"}.intersection(profile.body_fields):
        raise AgentError("external_mapping_invalid", "Dify 会自动填写消息、会话与用户字段。", 400)
    return HttpProfile(
        message_field="query",
        session_field="conversation_id",
        session_path="conversation_id",
        retrieval_path="metadata.retriever_resources",
        readback_path=profile.readback_path,
        readback_id_path=profile.readback_id_path,
        sandbox_tool_fault=profile.sandbox_tool_fault,
        body_fields={"inputs": {}, **profile.body_fields, "response_mode": "streaming"},
    )


def within_quota(settings, owner):
    directory = _base(settings) / str(owner)
    try:
        count = sum(KEY_ID.fullmatch(p.stem) is not None for p in directory.glob("*.agent"))
    except OSError:
        raise _store_error() from None
    if count >= 10:
        raise AgentError("external_self_limit", "每个账号最多登记 10 个自助 Agent 版本。", 409)


def _store_error():
    return AgentError("external_self_store", "暂时无法读取或保存你的 Agent 配置。", 503)


def read(settings, owner, identity):
    if not owner or not enabled(settings) or not KEY_ID.fullmatch(identity):
        return None
    path = _base(settings) / str(owner) / (identity + ".agent")
    try:
        with path.open("rb") as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError
        saved = Saved.model_validate_json(Fernet(_key(settings).encode("ascii")).decrypt(raw))
        if saved.owner != owner or saved.entry.id != identity:
            raise ValueError
        if not TOKEN.fullmatch(saved.access_token.get_secret_value()):
            raise ValueError
        public_ip(saved.entry.pinned_ip)
        return saved
    except FileNotFoundError:
        return None
    except (OSError, ValueError, InvalidToken):
        raise _store_error() from None


def owned(settings, owner):
    if enabled(settings):
        for path in sorted((_base(settings) / str(owner)).glob("*.agent")):
            item = read(settings, owner, path.stem)
            if item:
                yield item.entry


def save(settings, saved):
    # The caller holds the platform registration advisory lock.
    identity = saved.entry.id
    existing = read(settings, saved.owner, identity)
    if existing:
        if existing == saved:
            return
        raise AgentError("external_self_version_exists", "登记已变化，请使用新的服务版本号。", 409)
    within_quota(settings, saved.owner)
    path = _base(settings) / str(saved.owner) / (identity + ".agent")
    payload = saved.model_dump(exclude={"access_token"})
    payload["access_token"] = saved.access_token.get_secret_value()
    tmp = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        encrypted = Fernet(_key(settings).encode("ascii")).encrypt(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        )
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            tmp = Path(stream.name)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except (OSError, ValueError):
        raise _store_error() from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
