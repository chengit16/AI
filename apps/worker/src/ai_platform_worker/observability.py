"""为每个 Worker 进程装配独立可观测运行时。"""

from functools import lru_cache

from ai_platform_backend.observability import ObservabilityRuntime

from ai_platform_worker.config import get_worker_settings


@lru_cache
def get_worker_observability() -> ObservabilityRuntime:
    """按进程缓存运行时；不同 Lane 通过标签和结构化日志区分。"""

    settings = get_worker_settings()
    return ObservabilityRuntime(
        service_name=settings.app_name,
        environment=settings.environment,
        field_registry_path=settings.observability_field_registry_path,
        otlp_endpoint=settings.observability_otlp_endpoint,
        otlp_timeout_seconds=settings.observability_otlp_timeout_seconds,
        metrics_process_prefix=f"worker-{settings.worker_lane}",
    )


def shutdown_worker_observability() -> None:
    """在 Celery 子进程退出时清理 live Gauge，历史 Counter 由下次启动统一清理。"""

    if get_worker_observability.cache_info().currsize > 0:
        get_worker_observability().shutdown()
        get_worker_observability.cache_clear()
