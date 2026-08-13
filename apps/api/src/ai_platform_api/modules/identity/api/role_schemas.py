from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_key: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=120)


class RoleStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class RoleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    role_key: str
    name: str
    status: Literal["active", "disabled"]
    system_managed: bool
    version: int


class RoleListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RoleResponse]


class CreateRoleBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    scope_type: Literal["workspace", "department", "member"]
    department_id: UUID | None = None
    account_id: UUID | None = None

    @model_validator(mode="after")
    def validate_scope_target(self) -> CreateRoleBindingRequest:
        valid = (
            (
                self.scope_type == "workspace"
                and self.department_id is None
                and self.account_id is None
            )
            or (
                self.scope_type == "department"
                and self.department_id is not None
                and self.account_id is None
            )
            or (
                self.scope_type == "member"
                and self.department_id is None
                and self.account_id is not None
            )
        )
        if not valid:
            raise ValueError("角色绑定目标与范围类型不一致")
        return self


class RoleBindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: UUID
    role_id: UUID
    scope_type: Literal["workspace", "department", "member"]
    department_id: UUID | None
    membership_id: UUID | None
    status: Literal["active", "revoked"]
    version: int


class EffectiveRoleSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: Literal["workspace", "department", "member"]
    scope_id: UUID


class EffectiveRoleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    role_key: str
    name: str
    sources: list[EffectiveRoleSourceResponse]


class EffectiveRoleSetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    membership_id: UUID
    role_version: int
    roles: list[EffectiveRoleResponse]
