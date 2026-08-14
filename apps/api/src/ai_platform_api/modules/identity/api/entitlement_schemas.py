"""定义空间套餐、功能开关和用量响应 Schema。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class QuotaResponse(BaseModel):
    """定义额度操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    metric: Literal[
        "members",
        "storage_bytes",
        "knowledge_bases",
        "published_agents",
        "questions_monthly",
    ]
    period_key: str
    used_value: int
    limit_value: int
    remaining_value: int


class EntitlementResponse(BaseModel):
    """定义权益操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    workspace_status: Literal["active", "suspended", "archived"]
    plan_code: str
    entitlement_version: int
    open_api_allowed: bool
    open_api_enabled: bool
    public_publish_allowed: bool
    quotas: list[QuotaResponse]


class OpenApiFeatureRequest(BaseModel):
    """定义开放API功能操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
