from celery import Celery

from ai_platform_worker.config import get_worker_settings

settings = get_worker_settings()

celery_app = Celery(
    "ai-platform-worker",
    broker=settings.valkey_url,
    include=["ai_platform_worker.consumers.integration_events"],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_backend=None,
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "dispatch-outbox": {
            "task": "platform.outbox.dispatch.v1",
            "schedule": settings.outbox_dispatch_interval_seconds,
        },
        "process-ingestion-jobs": {
            "task": "platform.ingestion.process.v1",
            "schedule": settings.ingestion_dispatch_interval_seconds,
        },
    },
)
