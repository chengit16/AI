from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RolePermissionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission_code: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$")
    scope_type: Literal["workspace", "department_tree", "self", "resource"]
    department_ids: list[UUID] = Field(default_factory=list)
    resource_ids: list[UUID] = Field(default_factory=list)
    maximum_security_level: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] = (
        "RESTRICTED"
    )
    field_mask: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_targets(self) -> "RolePermissionEntry":
        if self.scope_type in {"workspace", "self"}:
            valid = not self.department_ids and not self.resource_ids
        elif self.scope_type == "department_tree":
            valid = bool(self.department_ids) and not self.resource_ids
        else:
            valid = bool(self.resource_ids) and not self.department_ids
        if not valid:
            raise ValueError("数据范围目标与 scope_type 不一致")
        if len(self.field_mask) != len(set(self.field_mask)) or any(
            not field_name.strip() for field_name in self.field_mask
        ):
            raise ValueError("字段遮罩必须是非空且不重复的字段名")
        return self


class ReplaceRolePermissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RolePermissionEntry] = Field(max_length=200)


class RolePermissionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RolePermissionEntry]
