"""定义组织树、岗位和成员归属接口的请求与响应 Schema。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateDepartmentRequest(BaseModel):
    """定义创建部门操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    parent_department_id: UUID | None = None


class MoveDepartmentRequest(BaseModel):
    """定义移动部门操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    parent_department_id: UUID | None = None


class OrganizationStatusRequest(BaseModel):
    """定义组织状态操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    active: bool


class DepartmentResponse(BaseModel):
    """定义部门操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    parent_department_id: UUID | None
    name: str
    status: Literal["active", "disabled"]
    effective_active: bool
    depth: int
    version: int


class DepartmentListResponse(BaseModel):
    """定义部门列表操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    items: list[DepartmentResponse]


class CreatePositionRequest(BaseModel):
    """定义创建职位操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    name: str = Field(min_length=1, max_length=120)


class PositionResponse(BaseModel):
    """定义职位操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    position_id: UUID
    department_id: UUID
    name: str
    status: Literal["active", "disabled"]
    effective_active: bool
    version: int


class PositionListResponse(BaseModel):
    """定义职位列表操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    items: list[PositionResponse]


class AssignMemberOrganizationRequest(BaseModel):
    """定义分配成员组织操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    department_ids: list[UUID] = Field(max_length=100)
    primary_department_id: UUID | None = None
    position_ids: list[UUID] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_primary_department(self) -> AssignMemberOrganizationRequest:
        has_departments = bool(self.department_ids)
        if has_departments != (self.primary_department_id is not None):
            raise ValueError("有部门归属时必须指定主部门")
        if (
            self.primary_department_id is not None
            and self.primary_department_id not in self.department_ids
        ):
            raise ValueError("主部门必须包含在部门归属中")
        return self


class MemberOrganizationResponse(BaseModel):
    """定义成员组织操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    department_ids: list[UUID]
    primary_department_id: UUID | None
    position_ids: list[UUID]
    membership_version: int
