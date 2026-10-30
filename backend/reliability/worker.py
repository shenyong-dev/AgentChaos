"""Reuse the production Linux Celery transport, injecting only an offline gateway."""

import os

from app.agents.errors import AgentError
from app.db.engine import create_database_engine
from app.executors.runner import redis_connection, run_execution
from app.workers.celery_app import celery_app, settings
from reliability.gateway import InfrastructureGateway

# This module is an opt-in lab entrypoint, never imported by the daily Worker.
celery_app.tasks.unregister("agentchaos.execute")


@celery_app.task(name="agentchaos.execute", soft_time_limit=180, time_limit=200)
def execute(execution_uuid):
    if os.environ.get("AC_T17_MODE", "offline") != "offline":
        raise AgentError("lab_mode_not_authorized", "本入口只运行明确离线控制。", 403)
    assert not settings.deepseek_api_key.get_secret_value()
    assert not settings.executor_allow_paid_model_calls
    assert settings.mysql_database.startswith("agentchaos_t17_verify_")
    engine = create_database_engine(settings)
    redis = redis_connection(settings)
    try:
        return run_execution(
            engine,
            redis,
            settings,
            execution_uuid,
            gateway_factory=lambda budget, control: InfrastructureGateway(control),
        )
    finally:
        redis.close()
        engine.dispose()
