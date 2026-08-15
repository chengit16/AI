"""创建 Celery 应用并只注册受治理的集成事件与后台扫描任务。"""

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
    task_default_queue="platform.control",
    task_routes={
        "platform.outbox.dispatch.v1": {"queue": "platform.control"},
        "platform.integration.consume.v1": {"queue": "platform.control"},
        "platform.ingestion.parse.v1": {"queue": "platform.parsing"},
        "platform.ingestion.ocr.v1": {"queue": "platform.ocr"},
        "platform.indexing.embed.v1": {"queue": "platform.embedding"},
        "platform.indexing.commit.v1": {"queue": "platform.indexing"},
    },
    worker_concurrency=settings.worker_concurrency,
    beat_schedule={
        "dispatch-outbox": {
            "task": "platform.outbox.dispatch.v1",
            "schedule": settings.outbox_dispatch_interval_seconds,
        },
        "process-parsing-jobs": {
            "task": "platform.ingestion.parse.v1",
            "schedule": settings.ingestion_dispatch_interval_seconds,
        },
        "process-ocr-jobs": {
            "task": "platform.ingestion.ocr.v1",
            "schedule": settings.ingestion_dispatch_interval_seconds,
        },
        "process-index-embeddings": {
            "task": "platform.indexing.embed.v1",
            "schedule": settings.indexing_dispatch_interval_seconds,
        },
        "commit-index-versions": {
            "task": "platform.indexing.commit.v1",
            "schedule": settings.indexing_dispatch_interval_seconds,
        },
    },
)
