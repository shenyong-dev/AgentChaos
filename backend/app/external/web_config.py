"""Console configuration limited to server-approved, pinned endpoints and credentials."""

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.external.registry import address, file_entries
from app.external.schema import PREFIX, PROTOCOL, Entry, Strict, unique_json


class Profile(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    label: str = Field(min_length=1, max_length=100)
    endpoint: str
    pinned_ip: str
    network_zone: Literal["public", "approved_private"] = "public"
    credential_env: str
    billing: Literal["free", "external_account"] = "free"
    cost_upper_microusd: int = 0
    token_upper: int = 8192
    default_timeout_seconds: int = 20
    author_relation: Literal["project_example", "external_developer"] = "external_developer"


class Connection(Strict):
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=100)
    revision: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    timeout_seconds: int = Field(ge=1, le=45)


class Policy(Strict):
    profiles: list[Profile] = Field(max_length=30)


class Connections(Strict):
    items: list["SavedConnection"] = Field(max_length=30)


class SavedConnection(Strict):
    connection: Connection
    entry_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def build(profile, draft, settings):
    entry = Entry.model_validate(
        {
            **profile.model_dump(exclude={"label", "billing", "default_timeout_seconds"}),
            "timeout_seconds": draft.timeout_seconds,
            "enabled": True,
            "card": {
                "protocol": PROTOCOL,
                "name": draft.name,
                "revision": draft.revision,
                "billing": profile.billing,
                "side_effects": False,
                "request_lookup": True,
                "cancellation": True,
                "cancel_before_execute": True,
            },
        }
    )
    address(entry, settings)
    return entry


def document(path, schema):
    if not path.is_absolute() or not 0 < path.stat().st_size <= 131072:
        raise ValueError("Invalid configuration path or size")
    return schema.model_validate(json.loads(path.read_bytes(), object_pairs_hook=unique_json))


def policy(settings):
    raw = settings.external_agent_web_policy_path
    if not raw:
        return {}
    try:
        profiles = document(Path(raw), Policy).profiles
        result = {item.id: item for item in profiles}
        if len(result) != len(profiles):
            raise ValueError("Duplicate profile")
        from app.external.self_service import KEY_ID

        for profile in profiles:
            if KEY_ID.fullmatch(profile.id):
                raise ValueError("Reserved personal agent identifier")
            draft = Connection(
                profile_id=profile.id,
                name=profile.label,
                revision="profile",
                timeout_seconds=profile.default_timeout_seconds,
            )
            build(profile, draft, settings)
        return result
    except (OSError, ValueError):
        raise AgentError(
            "external_web_policy_invalid", "网页接入策略不可用，请联系维护者。", 503
        ) from None


def profiles_for_ui(settings):
    fixed = file_entries(settings)
    return [
        item.model_dump(include={"id", "label", "endpoint", "default_timeout_seconds", "billing"})
        for item in policy(settings).values()
        if PREFIX + item.id not in fixed
    ]


def entry_for_draft(settings, draft):
    profile = policy(settings).get(draft.profile_id)
    if profile is None or PREFIX + draft.profile_id in file_entries(settings):
        raise AgentError(
            "external_web_profile_unavailable", "该服务地址尚未获准，请选择已批准的地址。", 409
        )
    return build(profile, draft, settings)


def store_path(settings):
    policy_path = Path(settings.external_agent_web_policy_path)
    deployment = canonical_hash([settings.mysql_database, settings.executor_namespace])[:16]
    return policy_path.with_name(policy_path.name + "." + deployment + ".connections.json")


def records(settings):
    try:
        rows = document(store_path(settings), Connections).items
        result = {row.connection.profile_id: row for row in rows}
        if len(result) != len(rows):
            raise ValueError("Duplicate connection")
        return result
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise AgentError(
            "external_web_store_invalid", "网页接入记录不可读，请联系维护者。", 503
        ) from None


def configured_entries(settings):
    allowed = policy(settings)
    result = {}
    for identity, row in records(settings).items():
        if identity in allowed:
            entry = build(allowed[identity], row.connection, settings)
            if canonical_hash(entry.model_dump()) == row.entry_hash:
                result[PREFIX + identity] = entry
    return result


def save_connection(settings, draft, entry_hash):
    """Caller holds the shared registration lock."""
    saved = records(settings)
    row = SavedConnection(connection=draft, entry_hash=entry_hash)
    if saved.get(draft.profile_id) == row:
        return
    if draft.profile_id in saved:
        raise AgentError(
            "external_web_connection_exists",
            "此服务已有配置。请复用原配置，或请维护者批准新的地址标识。",
            409,
        )
    saved[draft.profile_id] = row
    target = store_path(settings)
    temporary = None
    try:
        payload = Connections(items=list(saved.values())).model_dump_json()
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as out:
            temporary = Path(out.name)
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    except (OSError, ValueError):
        raise AgentError(
            "external_web_store_write", "暂时无法保存接入配置，请按原配置重试。", 503
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
