from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateDepartmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    parent_department_id: UUID | None = None


class MoveDepartmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_department_id: UUID | None = None


class OrganizationStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    parent_department_id: UUID | None
    name: str
    status: Literal["active", "disabled"]
    effective_active: bool
    depth: int
    version: int


class DepartmentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DepartmentResponse]


class CreatePositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    name: str = Field(min_length=1, max_length=120)


class PositionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_id: UUID
    department_id: UUID
    name: str
    status: Literal["active", "disabled"]
    effective_active: bool
    version: int


class PositionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PositionResponse]


class AssignMemberOrganizationRequest(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    department_ids: list[UUID]
    primary_department_id: UUID | None
    position_ids: list[UUID]
    membership_version: int
