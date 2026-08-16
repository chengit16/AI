"""验证 P1A-05 Worker 运行配置、任务路由和健康边界。"""

from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]
from ai_platform_worker.app.celery_app import celery_app

ROOT = Path(__file__).parents[1]


def load_compose() -> dict[str, Any]:
    document = yaml.safe_load((ROOT / "infra/compose/compose.yaml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("Compose 顶层必须是对象")
    return cast(dict[str, Any], document)


def test_compose_runs_migration_before_api_and_worker() -> None:
    services = load_compose()["services"]
    migrate = services["migrate"]
    worker_services = (
        "worker-control",
        "worker-parsing",
        "worker-ocr",
        "worker-embedding",
        "worker-indexing",
        "scheduler",
    )

    assert migrate["command"][:2] == ["sh", "-c"]
    assert "find /var/run/ai-platform-prometheus" in migrate["command"][2]
    assert "uv run --no-sync alembic upgrade head" in migrate["command"][2]
    assert migrate["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert migrate["restart"] == "no"
    assert services["api"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert all(
        services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        for name in worker_services
    )


def test_compose_exposes_each_secret_only_to_its_owner() -> None:
    services = load_compose()["services"]
    api = services["api"]
    workers = [service for name, service in services.items() if name.startswith("worker-")]

    assert "AI_PLATFORM_MASTER_KEY_PATH" in api["environment"]
    assert "AI_PLATFORM_TASK_SIGNING_KEY_PATH" not in api["environment"]
    assert all("AI_PLATFORM_TASK_SIGNING_KEY_PATH" in worker["environment"] for worker in workers)
    assert all("AI_PLATFORM_MASTER_KEY_PATH" not in worker["environment"] for worker in workers)
    api_secrets = [volume for volume in api["volumes"] if "/secrets/" in volume]
    assert len(api_secrets) == 1
    assert "/secrets/master.key:/run/secrets/ai-platform-master.key:ro" in api_secrets[0]
    assert all(
        len([volume for volume in worker["volumes"] if "/secrets/" in volume]) == 1
        and any(
            "/secrets/task-signing.key:/run/secrets/ai-platform-task-signing.key:ro" in volume
            for volume in worker["volumes"]
        )
        for worker in workers
    )
    assert "/runtime/prometheus:/var/run/ai-platform-prometheus" in "\n".join(api["volumes"])
    assert all(
        "/runtime/prometheus:/var/run/ai-platform-prometheus" in "\n".join(worker["volumes"])
        for worker in workers
    )
    assert all(
        "/run/secrets/ai-platform-task-signing.key" in worker["healthcheck"]["test"][1]
        for worker in workers
    )
    assert "AI_PLATFORM_TASK_SIGNING_KEY_PATH" not in services["scheduler"]["environment"]
    assert services["scheduler"]["volumes"] == []
    assert "beat" in services["scheduler"]["healthcheck"]["test"][1]


def test_celery_registers_versioned_tasks_and_reliable_delivery_options() -> None:
    celery_app.loader.import_default_modules()

    assert "platform.outbox.dispatch.v1" in celery_app.tasks
    assert "platform.integration.consume.v1" in celery_app.tasks
    assert "platform.ingestion.parse.v1" in celery_app.tasks
    assert "platform.ingestion.ocr.v1" in celery_app.tasks
    assert "platform.indexing.embed.v1" in celery_app.tasks
    assert "platform.indexing.commit.v1" in celery_app.tasks
    assert "platform.indexing.inspect.v1" in celery_app.tasks
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.result_backend is None
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.beat_schedule["dispatch-outbox"]["task"] == (
        "platform.outbox.dispatch.v1"
    )
    assert celery_app.conf.beat_schedule["process-parsing-jobs"]["task"] == (
        "platform.ingestion.parse.v1"
    )
    assert celery_app.conf.beat_schedule["process-ocr-jobs"]["task"] == (
        "platform.ingestion.ocr.v1"
    )
    assert celery_app.conf.beat_schedule["process-index-embeddings"]["task"] == (
        "platform.indexing.embed.v1"
    )
    assert celery_app.conf.beat_schedule["commit-index-versions"]["task"] == (
        "platform.indexing.commit.v1"
    )
    assert celery_app.conf.beat_schedule["inspect-index-consistency"]["task"] == (
        "platform.indexing.inspect.v1"
    )
    assert celery_app.conf.task_routes["platform.ingestion.ocr.v1"]["queue"] == "platform.ocr"
    assert celery_app.conf.task_routes["platform.indexing.commit.v1"]["queue"] == (
        "platform.indexing"
    )
    assert celery_app.conf.task_routes["platform.indexing.inspect.v1"]["queue"] == (
        "platform.indexing"
    )


def test_runtime_files_fix_shared_paths_and_health_checks() -> None:
    alembic = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    migration_environment = (ROOT / "infra/migrations/env.py").read_text(encoding="utf-8")
    api_dockerfile = (ROOT / "infra/docker/api.Dockerfile").read_text(encoding="utf-8")
    worker_dockerfile = (ROOT / "infra/docker/worker.Dockerfile").read_text(encoding="utf-8")
    platform_script = (ROOT / "platform").read_text(encoding="utf-8")

    assert "prepend_sys_path = apps/api/src:packages/backend/src" in alembic
    assert 'os.environ.get("AI_PLATFORM_DATABASE_URL")' in migration_environment
    assert "COPY contracts/lifecycle ./contracts/lifecycle" in api_dockerfile
    assert "PYTHONPATH=/app/apps/worker/src:/app/packages/backend/src" in worker_dockerfile
    assert 'CMD ["uv", "run", "--no-sync", "celery"' in worker_dockerfile
    assert '"--beat"' not in worker_dockerfile
    assert "SELECT version_num FROM public.alembic_version" in platform_script
    assert 'database_revision" == "20260816_0058"' in platform_script
    assert "AI_PLATFORM_MIN_FREE_DISK_GB:-50" in platform_script
    assert "worker-control worker-parsing worker-ocr worker-embedding worker-indexing" in (
        platform_script
    )
    assert "REQUIRED_RUNNING_SERVICES=(postgres valkey minio tika api web" in platform_script
    assert "compose ps --format json scheduler" in platform_script
