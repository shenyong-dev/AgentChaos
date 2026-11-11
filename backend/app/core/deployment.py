"""Non-secret identities for observed Worker source and actual MySQL binding."""

import hashlib

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.agents.versions import canonical_hash
from app.core.config import PROJECT_ROOT


def source_fingerprint():
    paths = sorted((PROJECT_ROOT / "backend/app").rglob("*.py"))
    paths += sorted((PROJECT_ROOT / "backend/app").rglob("*.txt"))
    paths += sorted((PROJECT_ROOT / "backend/app").rglob("*.json"))
    paths += sorted((PROJECT_ROOT / "backend/alembic/versions").glob("*.py"))
    paths += sorted((PROJECT_ROOT / "backend/reference_agents").rglob("*.py"))
    paths += sorted((PROJECT_ROOT / "backend/reference_agents").rglob("*.txt"))
    paths += [PROJECT_ROOT / "backend/uv.lock"]
    return canonical_hash(
        {
            p.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(
                p.read_text(encoding="utf-8").encode()
            ).hexdigest()
            for p in paths
        }
    )


def probe_engine(settings):
    return create_engine(
        settings.mysql_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": settings.dependency_timeout_seconds,
            "read_timeout": settings.dependency_timeout_seconds,
            "write_timeout": settings.dependency_timeout_seconds,
        },
    )


def database_binding(settings):
    engine = probe_engine(settings)
    try:
        with engine.connect() as connection:
            server, database = connection.execute(text("SELECT @@server_uuid, DATABASE()")).one()
        return canonical_hash([server, database, settings.executor_namespace])
    finally:
        engine.dispose()


def execution_fingerprint(settings):
    keys = (
        "agent_model",
        "agent_thinking",
        "agent_max_output_tokens",
        "agent_tool_policy",
        "executor_allow_paid_model_calls",
        "executor_budget_authorization_id",
        "executor_agent_budget_usd",
        "executor_agent_budget_unlimited",
        "cloud_deployment_id",
        "cloud_approval_sha256",
        "cloud_open_model_budget",
    )
    return canonical_hash(
        {
            **{k: getattr(settings, k) for k in keys},
            "credential_present": bool(settings.deepseek_api_key.get_secret_value()),
        }
    )
