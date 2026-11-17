"""Server-owned registry; HTTP requests select only a previously registered kind."""

import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.external.schema import PREFIX, Entry, Registry, unique_json


def is_external(kind):
    return isinstance(kind, str) and kind.startswith(PREFIX)


def address(entry, settings):
    try:
        url = urlsplit(entry.endpoint)
        ip = ipaddress.ip_address(entry.pinned_ip)
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_/-]*", url.path)
            or url.port == 0
            or ip.is_unspecified
            or ip.is_multicast
            or ip.is_link_local
            or (ip.version == 6 and ip.ipv4_mapped is not None)
        ):
            raise ValueError("Endpoint not allowed")
        port = url.port or (443 if url.scheme == "https" else 80)
        if entry.network_zone == "public":
            if not ip.is_global or url.scheme != "https":
                raise ValueError("Public targets require HTTPS and public pinned IP")
        elif url.scheme != "https" and not (
            url.scheme == "http" and ip.is_loopback and settings.app_env in {"development", "test"}
        ):
            raise ValueError("Private targets require HTTPS, except explicit local development")
        return url.hostname, port, str(ip)
    except ValueError:
        raise AgentError(
            "external_address_invalid", "外部对象地址未满足服务端登记策略。", 503
        ) from None


def entries(settings):
    path = settings.external_agent_registry_path
    if not path:
        return {}
    try:
        source = Path(path)
        if not source.is_absolute() or not 0 < source.stat().st_size <= 131072:
            raise ValueError("Invalid registry")
        registry = Registry.model_validate(
            json.loads(source.read_bytes(), object_pairs_hook=unique_json)
        )
        for entry in registry.targets:
            address(entry, settings)
        return {PREFIX + entry.id: entry for entry in registry.targets}
    except (OSError, ValueError):
        raise AgentError(
            "external_registry_invalid", "外部对象登记文件无效，请由维护者核对。", 503
        ) from None


def entry_for(settings, kind) -> Entry:
    entry = entries(settings).get(kind)
    if entry is None or not entry.enabled:
        raise AgentError("external_target_unavailable", "外部对象未登记或已停用。", 409)
    return entry


def credential(entry):
    value = os.environ.get(entry.credential_env, "")
    if not 16 <= len(value) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise AgentError("external_credential_missing", "外部对象服务凭据尚未配置。", 503)
    return value


def fingerprint(settings):
    return canonical_hash({kind: entry.model_dump() for kind, entry in entries(settings).items()})


def supported(settings, kind):
    if kind in {"langgraph_booking", "reference_refund"}:
        return True
    entry = entries(settings).get(kind) if is_external(kind) else None
    return bool(entry and entry.enabled)
