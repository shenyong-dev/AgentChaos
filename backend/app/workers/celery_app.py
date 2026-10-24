"""Queue wiring only. Business task claiming/leases are implemented in T05."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("agentchaos", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_default_queue="agentchaos",
    broker_transport_options={"global_keyprefix": "ac:queue:"},
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    enable_utc=True,
    timezone="UTC",
)
