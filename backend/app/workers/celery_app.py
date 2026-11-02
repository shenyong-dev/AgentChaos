"""Linux Celery transport; MySQL leases provide the effective execution boundary."""

from celery import Celery
from celery.worker.control import inspect_command

from app.core.config import get_settings
from app.core.deployment import database_binding, execution_fingerprint, source_fingerprint

settings = get_settings()
IMPORTED_SOURCE = source_fingerprint()
celery_app = Celery("agentchaos", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_default_queue=settings.executor_queue,
    broker_transport_options={
        "global_keyprefix": settings.executor_namespace + ":queue:",
        "visibility_timeout": 300,
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
    },
    broker_connection_timeout=3,
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    enable_utc=True,
    timezone="UTC",
)


@inspect_command()
def agentchaos_readiness(state):
    """Read actual consumer queues and binding; never execute or dispatch a task."""
    try:
        binding = database_binding(settings)
    except Exception:
        binding = None
    return {
        "protocol": "T17C-1",
        "source_hash": IMPORTED_SOURCE,
        "database_binding": binding,
        "execution_hash": execution_fingerprint(settings),
        "queues": [q.name for q in state.consumer.task_consumer.queues],
    }


@celery_app.task(name="agentchaos.execute", soft_time_limit=920, time_limit=940)
def execute(execution_uuid):
    from app.db.engine import create_database_engine
    from app.executors.runner import redis_connection, run_execution

    engine = create_database_engine(settings)
    redis = redis_connection(settings)
    try:
        return run_execution(engine, redis, settings, execution_uuid)
    finally:
        redis.close()
        engine.dispose()
