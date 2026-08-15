"""提供进程存活与受配置控制的外部依赖就绪探针。"""

import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated
from urllib.parse import urlparse
from urllib.request import urlopen

from ai_platform_backend.observability import ObservabilityRuntime
from fastapi import APIRouter, Depends, Request, Response, status

from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.system.api.schemas import (
    HealthCheckDetail,
    HealthResponse,
    HealthStatus,
)

router = APIRouter(prefix="/health", tags=["系统健康"])


@dataclass(frozen=True)
class DependencyHealth:
    """保存一次依赖探测的安全结果；原始异常和连接地址不会越过探针边界。"""

    status: HealthStatus
    critical: bool
    latency_ms: float
    checked_at: datetime
    reason_code: str | None = None


def check_tcp(url: str, default_port: int) -> bool:
    """处理检查TCP，并保持调用方可依赖的稳定返回语义。"""

    parsed = urlparse(url)
    if parsed.hostname is None:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port or default_port), timeout=1):
            return True
    except OSError:
        return False


def check_http(url: str, path: str) -> bool:
    """处理检查HTTP，并保持调用方可依赖的稳定返回语义。"""

    try:
        with urlopen(f"{url.rstrip('/')}{path}", timeout=2) as response:
            return bool(200 <= response.status < 300)
    except OSError:
        return False


def dependency_checks(settings: Settings) -> dict[str, bool]:
    """保留阶段 1 的布尔兼容接口；详细延迟由新健康事实单独承载。"""

    return {
        name: result.status == "ok" for name, result in dependency_health_checks(settings).items()
    }


def dependency_health_checks(settings: Settings) -> dict[str, DependencyHealth]:
    """逐项测量依赖状态和耗时，任何异常均收敛为稳定降级码。"""

    checks: dict[str, Callable[[], bool]] = {
        "postgres": lambda: check_tcp(settings.database_url, 5432),
        "valkey": lambda: check_tcp(settings.valkey_url, 6379),
        "object_storage": lambda: check_http(settings.minio_endpoint, "/minio/health/live"),
        "document_parser": lambda: check_http(settings.tika_url, "/version"),
    }
    return {name: _measure_dependency(check) for name, check in checks.items()}


def _measure_dependency(check: Callable[[], bool]) -> DependencyHealth:
    started = monotonic()
    checked_at = datetime.now(UTC)
    try:
        healthy = check()
    except Exception:
        # 探针必须失败关闭，但不能把驱动、连接地址或异常文本复制到响应和日志。
        healthy = False
    return DependencyHealth(
        status="ok" if healthy else "degraded",
        critical=True,
        latency_ms=round(max(0, monotonic() - started) * 1000, 3),
        checked_at=checked_at,
        reason_code=None if healthy else "dependency_unavailable",
    )


@router.get(
    "/live",
    response_model=HealthResponse,
    response_model_exclude_none=True,
    operation_id="getLiveness",
)
def get_liveness(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """获取存活检查，并保持调用方可依赖的稳定返回语义。"""

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
    response_model_exclude_none=True,
    operation_id="getReadiness",
    responses={503: {"model": HealthResponse, "description": "依赖尚未就绪"}},
)
def get_readiness(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    # 非容器单元测试只验证进程与配置；容器环境显式开启全部外部依赖探测。
    """获取就绪检查，并保持调用方可依赖的稳定返回语义。"""

    checked_at = datetime.now(UTC)
    details = {
        "api": DependencyHealth("ok", True, 0, checked_at),
        "configuration": DependencyHealth("ok", True, 0, checked_at),
    }
    if settings.dependency_checks_enabled:
        details.update(dependency_health_checks(settings))
    ready = all(item.status == "ok" for item in details.values() if item.critical)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    _record_dependency_health(request, details)
    return HealthResponse(
        service=settings.app_name,
        status="ok" if ready else "degraded",
        version=settings.version,
        environment=settings.environment,
        checks={name: item.status for name, item in details.items()},
        checked_at=checked_at,
        details={
            name: HealthCheckDetail(
                status=item.status,
                critical=item.critical,
                latency_ms=item.latency_ms,
                checked_at=item.checked_at,
                reason_code=item.reason_code,
            )
            for name, item in details.items()
        },
    )


def _record_dependency_health(
    request: Request,
    details: dict[str, DependencyHealth],
) -> None:
    """把健康事实写入固定指标和结构化日志，依赖名称来自服务端常量。"""

    # 1. 健康接口可在最小测试应用中独立使用，未装配运行时时保持原响应语义。
    runtime = getattr(request.app.state, "observability", None)
    if not isinstance(runtime, ObservabilityRuntime):
        return
    # 2. 每项依赖只输出固定名称、状态和耗时；关键降级同时生成受控告警事实。
    for dependency, detail in details.items():
        labels = {**runtime.common_labels, "dependency": dependency}
        runtime.fields.validate("metric", labels)
        runtime.dependency_health.labels(**labels).set(1 if detail.status == "ok" else 0)
        runtime.dependency_duration.labels(**labels).set(detail.latency_ms / 1000)
        if detail.reason_code is not None:
            runtime.log(
                "dependency_health_checked",
                component="health",
                operation="check",
                dependency=dependency,
                outcome=detail.status,
                duration_ms=detail.latency_ms,
                degraded_reason=detail.reason_code,
            )
        else:
            runtime.log(
                "dependency_health_checked",
                component="health",
                operation="check",
                dependency=dependency,
                outcome=detail.status,
                duration_ms=detail.latency_ms,
            )
        if detail.critical and detail.status == "degraded":
            runtime.alert(
                alert_name="dependency_unavailable",
                component="health",
                severity="critical",
                status="firing",
                reason_code=detail.reason_code or "dependency_unavailable",
                threshold=1,
                observed_value=0,
            )
