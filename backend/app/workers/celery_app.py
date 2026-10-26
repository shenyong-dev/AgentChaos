"""Linux Celery transport; MySQL leases provide the effective execution boundary."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()
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
