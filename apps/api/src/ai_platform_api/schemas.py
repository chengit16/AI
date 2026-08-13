from typing import Literal

from pydantic import BaseModel, ConfigDict

HealthStatus = Literal["ok", "degraded"]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    status: HealthStatus
    version: str
    environment: str
    checks: dict[str, HealthStatus]
