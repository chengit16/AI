from typing import Annotated

from fastapi import APIRouter, Depends

from ai_platform_api.config import Settings, get_settings
from ai_platform_api.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["系统健康"])


@router.get("/live", response_model=HealthResponse, operation_id="getLiveness")
def get_liveness(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        status="ok",
        version=settings.version,
        environment=settings.environment,
        checks={"api": "ok"},
    )


@router.get("/ready", response_model=HealthResponse, operation_id="getReadiness")
def get_readiness(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    # 阶段 0 的最小就绪条件只包含进程与配置；数据库等依赖在容器节点接入后扩展。
    return HealthResponse(
        service=settings.app_name,
        status="ok",
        version=settings.version,
        environment=settings.environment,
        checks={"api": "ok", "configuration": "ok"},
    )
