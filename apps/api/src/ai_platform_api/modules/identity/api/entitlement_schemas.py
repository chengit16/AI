from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class QuotaResponse(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    enabled: bool
