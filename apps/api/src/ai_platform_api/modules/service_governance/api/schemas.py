"""定义服务管理、访问策略和灰度路由请求响应 Schema。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateServiceRequest(BaseModel):
    """从一个有效 Agent Release 创建稳定服务身份。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    release_id: UUID
    service_type: Literal["custom_knowledge_agent", "scenario_application", "open_api"]
    visibility: Literal["workspace", "restricted"]
    allowed_department_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    allowed_account_ids: tuple[UUID, ...] = Field(default=(), max_length=100)


class UpdateServiceRequest(BaseModel):
    """按服务版本更新名称、状态或受众策略。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target_status: Literal["active", "suspended", "archived"] | None = None
    visibility: Literal["workspace", "restricted"] | None = None
    allowed_department_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    allowed_account_ids: tuple[UUID, ...] = Field(default=(), max_length=100)


class StartServiceCanaryRequest(BaseModel):
    """为当前服务追加稳定百分比灰度 Route。"""

    model_config = ConfigDict(extra="forbid")

    release_id: UUID
    canary_percent: int = Field(ge=1, le=99)
    expected_generation: int = Field(ge=1)


class PromoteServiceRouteRequest(BaseModel):
    """把指定 Release 晋级为唯一正式版本。"""

    model_config = ConfigDict(extra="forbid")

    release_id: UUID
    expected_generation: int = Field(ge=1)


class RollbackServiceRouteRequest(BaseModel):
    """按当前 generation 回滚到最近稳定版本。"""

    model_config = ConfigDict(extra="forbid")

    expected_generation: int = Field(ge=1)


class ServiceResponse(BaseModel):
    """表示工作空间内的稳定服务定义。"""

    model_config = ConfigDict(extra="forbid")

    service_id: UUID
    agent_id: UUID
    service_key: str
    name: str
    service_type: str
    status: str
    version: int
    updated_at: datetime


class ServiceAccessPolicyResponse(BaseModel):
    """表示当前不可变访问策略版本。"""

    model_config = ConfigDict(extra="forbid")

    access_policy_version_id: UUID
    version: int
    visibility: Literal["workspace", "restricted"]
    allowed_department_ids: tuple[UUID, ...]
    allowed_account_ids: tuple[UUID, ...]
    policy_hash: str


class ServiceRouteResponse(BaseModel):
    """表示当前不可变路由与灰度分配。"""

    model_config = ConfigDict(extra="forbid")

    route_id: UUID
    route_version: int
    route_mode: Literal["active", "canary", "rollback"]
    primary_release_id: UUID
    canary_release_id: UUID | None
    canary_percent: int
    previous_route_id: UUID | None
    route_hash: str
    created_at: datetime


class ServicePublicationResponse(BaseModel):
    """表示当前路由指针及并发控制 generation。"""

    model_config = ConfigDict(extra="forbid")

    route_id: UUID
    generation: int
    published_at: datetime


class ServiceDeploymentResponse(BaseModel):
    """组合服务、当前策略、Route 和发布指针。"""

    model_config = ConfigDict(extra="forbid")

    service: ServiceResponse
    access_policy: ServiceAccessPolicyResponse
    route: ServiceRouteResponse
    publication: ServicePublicationResponse


class ServiceListResponse(BaseModel):
    """返回当前空间可管理的服务。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ServiceDeploymentResponse]
