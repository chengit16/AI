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

    assert migrate["command"] == ["uv", "run", "--no-sync", "alembic", "upgrade", "head"]
    assert migrate["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert migrate["restart"] == "no"
    assert services["api"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["worker"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )


def test_compose_exposes_each_secret_only_to_its_owner() -> None:
    services = load_compose()["services"]
    api = services["api"]
    worker = services["worker"]

    assert "AI_PLATFORM_MASTER_KEY_PATH" in api["environment"]
    assert "AI_PLATFORM_TASK_SIGNING_KEY_PATH" not in api["environment"]
    assert "AI_PLATFORM_TASK_SIGNING_KEY_PATH" in worker["environment"]
    assert "AI_PLATFORM_MASTER_KEY_PATH" not in worker["environment"]
    assert len(api["volumes"]) == 1
    assert "/secrets/master.key:/run/secrets/ai-platform-master.key:ro" in api["volumes"][0]
    assert len(worker["volumes"]) == 1
    assert (
        "/secrets/task-signing.key:/run/secrets/ai-platform-task-signing.key:ro"
        in worker["volumes"][0]
    )


def test_celery_registers_versioned_tasks_and_reliable_delivery_options() -> None:
    celery_app.loader.import_default_modules()

    assert "platform.outbox.dispatch.v1" in celery_app.tasks
    assert "platform.integration.consume.v1" in celery_app.tasks
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.result_backend is None
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.beat_schedule["dispatch-outbox"]["task"] == (
        "platform.outbox.dispatch.v1"
    )


def test_runtime_files_fix_shared_paths_and_health_checks() -> None:
    alembic = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    migration_environment = (ROOT / "infra/migrations/env.py").read_text(encoding="utf-8")
    worker_dockerfile = (ROOT / "infra/docker/worker.Dockerfile").read_text(encoding="utf-8")
    platform_script = (ROOT / "platform").read_text(encoding="utf-8")

    assert "prepend_sys_path = apps/api/src:packages/backend/src" in alembic
    assert 'os.environ.get("AI_PLATFORM_DATABASE_URL")' in migration_environment
    assert "PYTHONPATH=/app/apps/worker/src:/app/packages/backend/src" in worker_dockerfile
    assert 'CMD ["uv", "run", "--no-sync", "celery"' in worker_dockerfile
    assert "SELECT version_num FROM public.alembic_version" in platform_script
    assert 'database_revision" == "20260814_0008"' in platform_script
    assert 'inspect ping --destination "celery@$HOSTNAME" --timeout 3' in platform_script
