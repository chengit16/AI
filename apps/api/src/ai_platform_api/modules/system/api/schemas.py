"""定义平台健康与依赖检查响应 Schema。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HealthStatus = Literal["ok", "degraded"]


class HealthCheckDetail(BaseModel):
    """描述单项检查的状态、时延和稳定降级原因，不包含连接地址或异常文本。"""

    model_config = ConfigDict(extra="forbid")

    status: HealthStatus
    critical: bool
    latency_ms: float = Field(ge=0)
    checked_at: datetime
    reason_code: str | None = None


class HealthResponse(BaseModel):
    """定义健康状态操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    service: str
    status: HealthStatus
    version: str
    environment: str
    checks: dict[str, HealthStatus]
    checked_at: datetime | None = None
    details: dict[str, HealthCheckDetail] | None = None
