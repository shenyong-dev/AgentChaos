"""Explicit T17H offline verification entrypoint; never used by the daily CLI default."""

from app.db.engine import create_database_engine
from app.executors.runner import redis_connection, run_execution
from app.workers.celery_app import celery_app as celery_app
from app.workers.celery_app import settings
from reliability.gateway import InfrastructureGateway

assert settings.mysql_database.startswith("agentchaos_t17h_verify_")
assert not settings.deepseek_api_key.get_secret_value()
assert not any(
    (
        settings.allow_paid_model_calls,
        settings.executor_allow_paid_model_calls,
        settings.casegen_allow_paid_model_calls,
        settings.judge_allow_paid_model_calls,
    )
)
celery_app.conf.ac_gateway_mode = "offline_test_double"
celery_app.tasks.unregister("agentchaos.execute")


@celery_app.task(name="agentchaos.execute", soft_time_limit=180, time_limit=200)
def execute(identifier):
    engine = create_database_engine(settings)
    client = redis_connection(settings)
    try:
        return run_execution(
            engine,
            client,
            settings,
            identifier,
            gateway_factory=lambda budget, control: InfrastructureGateway(control),
        )
    finally:
        client.close()
        engine.dispose()
