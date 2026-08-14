"""定义平台健康与依赖检查响应 Schema。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

HealthStatus = Literal["ok", "degraded"]


class HealthResponse(BaseModel):
    """定义健康状态操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    service: str
    status: HealthStatus
    version: str
    environment: str
    checks: dict[str, HealthStatus]
