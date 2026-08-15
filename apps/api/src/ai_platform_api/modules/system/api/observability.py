"""暴露仅含低基数运行指标的 Prometheus 抓取入口。"""

from ai_platform_backend.observability import ObservabilityRuntime
from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST

router = APIRouter(tags=["系统可观测性"])


@router.get("/metrics", include_in_schema=False)
def get_metrics(request: Request) -> Response:
    """返回当前 API 进程指标；字段注册表禁止正文和资源标识进入标签。"""

    runtime = getattr(request.app.state, "observability", None)
    if not isinstance(runtime, ObservabilityRuntime):
        raise RuntimeError("可观测运行时尚未装配")
    return Response(content=runtime.metrics_payload(), media_type=CONTENT_TYPE_LATEST)
