from datetime import datetime
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


class WorkspaceMenuOverrideEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_id: UUID
    parent_menu_id: UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    icon_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    sort_order: int = Field(ge=0)
    visible: bool
    version: int = Field(default=1, ge=1)


class ReplaceWorkspaceMenuConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceMenuOverrideEntry] = Field(max_length=500)


class WorkspaceMenuConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    menu_version: int = Field(ge=1)
    items: list[WorkspaceMenuOverrideEntry]


class RoleMenuVisibilityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_id: UUID
    visible: bool


class ReplaceRoleMenuVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RoleMenuVisibilityEntry] = Field(max_length=500)


class RoleMenuVisibilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    items: list[RoleMenuVisibilityEntry]


class MenuReleaseDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_reason(self) -> "MenuReleaseDecisionRequest":
        if not self.approved and (self.reason is None or not self.reason.strip()):
            raise ValueError("拒绝发布时必须填写原因")
        return self


class MenuReleaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_id: UUID
    workspace_id: UUID
    release_number: int = Field(ge=1)
    release_kind: Literal["standard", "rollback"]
    source_release_id: UUID | None
    status: Literal["draft", "validated", "approved", "rejected", "published"]
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_schema_version: int = Field(ge=1)
    registry_version: int = Field(ge=1)
    menu_version: int = Field(ge=1)
    menu_count: int = Field(ge=0)
    role_menu_count: int = Field(ge=0)
    menu_api_binding_count: int = Field(ge=0)
    validation_errors: list[str]
    rejection_reason: str | None
    created_by_account_id: UUID
    decided_by_account_id: UUID | None
    created_at: datetime
    validated_at: datetime | None
    decided_at: datetime | None
    published_at: datetime | None
    version: int = Field(ge=1)


class MenuReleaseSnapshotMenuEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_id: UUID
    menu_key: str
    parent_menu_id: UUID | None
    name: str
    menu_type: Literal["directory", "page", "action"]
    page_resource_id: UUID | None
    permission_code: str | None
    icon_key: str | None
    sort_order: int = Field(ge=0)
    source: Literal["system", "workspace"]
    status: Literal["active", "disabled"]
    visible: bool


class MenuReleaseSnapshotRoleMenuEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    menu_id: UUID
    visible: bool


class MenuReleaseSnapshotApiBindingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_id: UUID
    api_resource_id: UUID
    action_type: Literal["query", "mutation", "publish", "approve"]


class MenuReleaseSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    registry_version: int = Field(ge=1)
    workspace_id: UUID
    menu_version: int = Field(ge=1)
    menus: list[MenuReleaseSnapshotMenuEntry]
    role_menus: list[MenuReleaseSnapshotRoleMenuEntry]
    menu_api_bindings: list[MenuReleaseSnapshotApiBindingEntry]


class MenuReleaseListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MenuReleaseResponse]


class CurrentMenuReleaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: MenuReleaseResponse | None
    snapshot: MenuReleaseSnapshotResponse | None
