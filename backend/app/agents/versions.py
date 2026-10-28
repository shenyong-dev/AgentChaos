"""Immutable V0 configuration/source manifest, separate from dialogue evidence."""

import hashlib
import json
from importlib.metadata import version

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agents.budget import PRICING
from app.agents.context import CONTEXT_POLICY
from app.agents.errors import AgentError
from app.core.config import PROJECT_ROOT
from app.models import Target, TargetVersion


def canonical_hash(data):
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_manifest(settings, retriever, registry):
    source_paths = sorted((PROJECT_ROOT / "backend/app").rglob("*.py"))
    source_paths += sorted((PROJECT_ROOT / "backend/alembic/versions").glob("*.py"))
    source_paths += [
        PROJECT_ROOT / path for path in ("backend/app/agents/prompt.txt", "backend/uv.lock")
    ]
    sources = {
        path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(
            path.read_text(encoding="utf-8").encode()
        ).hexdigest()
        for path in source_paths
    }
    prompt_hash = sources["backend/app/agents/prompt.txt"]
    probe_path = PROJECT_ROOT / "docs/evidence/T04/model-probe.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.exists() else {}
    manifest = {
        "baseline": "V0",
        "provider": "DeepSeek",
        "role": "agent",
        "base_url": settings.deepseek_base_url,
        "model_requested": settings.agent_model,
        "thinking": settings.agent_thinking,
        "max_output_tokens": settings.agent_max_output_tokens,
        "sampling": "not asserted; temperature/top_p omitted",
        "sdk_versions": {
            name: version(name)
            for name in (
                "langgraph",
                "langchain-core",
                "langchain-deepseek",
                "langgraph-checkpoint-redis",
            )
        },
        "protocol_probe": {
            key: probe.get(key, "not_verified")
            for key in (
                "checked_at",
                "tool_arguments",
                "tool_call_id_roundtrip",
                "usage_returned",
                "stream_content_matches_final",
            )
        },
        "response_model": [
            record["response"]["model_returned"] for record in probe.get("records", [])
        ],
        "underlying_version": "not independently observable; official mapping recorded in pricing",
        "prompt_hash": prompt_hash,
        "sources": sources,
        "pricing": PRICING,
        "retrieval": retriever.manifest,
        "tools": registry.manifest,
        "context": CONTEXT_POLICY,
        "validation_boundary": "protocol evidence here; dialogue outcomes recorded separately",
    }
    manifest["config_hash"] = canonical_hash(manifest)
    return manifest


def register_version(engine, manifest):
    with engine.connect() as connection:
        locked = connection.scalar(text("SELECT GET_LOCK('ac:t04:version-register',5)")) == 1
        connection.commit()
        if not locked:
            raise AgentError("version_register_busy", "版本登记正在进行。", 503)
        try:
            with Session(connection) as session, session.begin():
                target = session.scalar(
                    select(Target)
                    .where(Target.adapter_type == "langgraph_booking")
                    .order_by(Target.id)
                )
                if target is None:
                    raise AgentError("target_not_seeded", "请先装入实验对象种子。", 503)
                existing = session.scalar(
                    select(TargetVersion).where(
                        TargetVersion.target_id == target.id,
                        TargetVersion.config_hash == manifest["config_hash"],
                    )
                )
                if existing:
                    return existing.id
                next_revision = (
                    session.scalar(
                        select(func.max(TargetVersion.version_no)).where(
                            TargetVersion.target_id == target.id
                        )
                    )
                    or 0
                ) + 1
                row = TargetVersion(
                    target_id=target.id,
                    version_no=next_revision,
                    model_config_json=manifest,
                    prompt_hash=manifest["prompt_hash"],
                    graph_revision="T04-V0-" + manifest["config_hash"][:12],
                    kb_manifest_json=manifest["retrieval"],
                    tool_revision="booking-registry-v1",
                    adapter_revision="langgraph-booking-v1",
                    config_hash=manifest["config_hash"],
                )
                session.add(row)
                session.flush()
                target.capabilities_json = {
                    "agent_implemented": True,
                    "stage": "T04",
                    "protocol_verified": manifest["protocol_probe"]["tool_call_id_roundtrip"]
                    == "passed",
                    "dialogue_validation": "see task evidence; not inferred from registration",
                }
                return row.id
        finally:
            connection.execute(text("SELECT RELEASE_LOCK('ac:t04:version-register')"))
            connection.commit()
