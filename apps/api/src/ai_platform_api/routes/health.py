import socket
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlparse
from urllib.request import urlopen

from fastapi import APIRouter, Depends, Response, status

from ai_platform_api.config import Settings, get_settings
from ai_platform_api.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["系统健康"])


def check_tcp(url: str, default_port: int) -> bool:
    parsed = urlparse(url)
    if parsed.hostname is None:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port or default_port), timeout=1):
            return True
    except OSError:
        return False


def check_http(url: str, path: str) -> bool:
    try:
        with urlopen(f"{url.rstrip('/')}{path}", timeout=2) as response:
            return bool(200 <= response.status < 300)
    except OSError:
        return False


def dependency_checks(settings: Settings) -> dict[str, bool]:
    checks: dict[str, Callable[[], bool]] = {
        "postgres": lambda: check_tcp(settings.database_url, 5432),
        "redis": lambda: check_tcp(settings.redis_url, 6379),
        "object_storage": lambda: check_http(settings.minio_endpoint, "/minio/health/live"),
        "document_parser": lambda: check_http(settings.tika_url, "/version"),
    }
    return {name: check() for name, check in checks.items()}


@router.get("/live", response_model=HealthResponse, operation_id="getLiveness")
def get_liveness(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        status="ok",
        version=settings.version,
        environment=settings.environment,
        checks={"api": "ok"},
    )


@router.get(
    "/ready",
    response_model=HealthResponse,
    operation_id="getReadiness",
    responses={503: {"model": HealthResponse, "description": "依赖尚未就绪"}},
)
def get_readiness(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    # 非容器单元测试只验证进程与配置；容器环境显式开启全部外部依赖探测。
    checks = {"api": True, "configuration": True}
    if settings.dependency_checks_enabled:
        checks.update(dependency_checks(settings))
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        service=settings.app_name,
        status="ok" if ready else "degraded",
        version=settings.version,
        environment=settings.environment,
        checks={name: "ok" if value else "degraded" for name, value in checks.items()},
    )
