"""定义统一权限与菜单发布接口的请求、响应和输入校验 Schema。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RolePermissionEntry(BaseModel):
    """定义角色权限条目的协议字段和序列化边界。"""

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
    """定义替换角色权限集合操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    expected_role_version: int | None = Field(default=None, ge=1)
    items: list[RolePermissionEntry] = Field(max_length=200)


class RolePermissionListResponse(BaseModel):
    """定义角色权限列表操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    role_version: int | None = Field(default=None, ge=1)
    items: list[RolePermissionEntry]


class PermissionFieldCatalogResponse(BaseModel):
    """定义权限矩阵可配置字段及其敏感级别。"""

    model_config = ConfigDict(extra="forbid")

    field_name: str
    security_level: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


class PermissionCatalogItemResponse(BaseModel):
    """定义权限目录中的权限码、资源动作和可选治理维度。"""

    model_config = ConfigDict(extra="forbid")

    permission_code: str
    resource_type: str
    action: str
    allowed_scope_types: list[Literal["workspace", "department_tree", "self", "resource"]]
    fields: list[PermissionFieldCatalogResponse]


class PermissionCatalogGroupResponse(BaseModel):
    """按产品域分组返回活动权限目录。"""

    model_config = ConfigDict(extra="forbid")

    domain: str
    items: list[PermissionCatalogItemResponse]


class RoleBindingSummaryResponse(BaseModel):
    """定义角色绑定的服务端可信来源摘要。"""

    model_config = ConfigDict(extra="forbid")

    scope_type: Literal["workspace", "department", "member"]
    scope_id: UUID
    scope_name: str


class RoleAffectedMemberResponse(BaseModel):
    """定义角色实际影响的低敏成员及有效来源。"""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    display_name: str
    membership_type: Literal["owner", "member"]
    sources: list[RoleBindingSummaryResponse]


class RoleGovernanceRoleResponse(BaseModel):
    """定义权限治理页中的角色、授权和影响成员聚合。"""

    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    role_key: str
    name: str
    status: Literal["active", "disabled"]
    system_managed: bool
    editable: bool
    grants: list[RolePermissionEntry]
    bindings: list[RoleBindingSummaryResponse]
    affected_member_count: int = Field(ge=0)
    affected_members: list[RoleAffectedMemberResponse]


class RoleGovernanceResponse(BaseModel):
    """定义权限治理页一次读取使用的稳定聚合响应。"""

    model_config = ConfigDict(extra="forbid")

    role_version: int = Field(ge=1)
    roles: list[RoleGovernanceRoleResponse]
    permission_groups: list[PermissionCatalogGroupResponse]


class WorkspaceMenuOverrideEntry(BaseModel):
    """定义工作空间菜单覆盖条目的协议字段和序列化边界。"""

    model_config = ConfigDict(extra="forbid")

    menu_id: UUID
    parent_menu_id: UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    icon_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    sort_order: int = Field(ge=0)
    visible: bool
    version: int = Field(default=1, ge=1)


class ReplaceWorkspaceMenuConfigurationRequest(BaseModel):
    """定义替换工作空间菜单配置操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceMenuOverrideEntry] = Field(max_length=500)


class WorkspaceMenuConfigurationResponse(BaseModel):
    """定义工作空间菜单配置操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    menu_version: int = Field(ge=1)
    items: list[WorkspaceMenuOverrideEntry]


class RoleMenuVisibilityEntry(BaseModel):
    """定义角色菜单可见性条目的协议字段和序列化边界。"""

    model_config = ConfigDict(extra="forbid")

    menu_id: UUID
    visible: bool


class ReplaceRoleMenuVisibilityRequest(BaseModel):
    """定义替换角色菜单可见性操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    items: list[RoleMenuVisibilityEntry] = Field(max_length=500)


class RoleMenuVisibilityResponse(BaseModel):
    """定义角色菜单可见性操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    items: list[RoleMenuVisibilityEntry]


class MenuReleaseDecisionRequest(BaseModel):
    """定义菜单发布决策操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_reason(self) -> "MenuReleaseDecisionRequest":
        if not self.approved and (self.reason is None or not self.reason.strip()):
            raise ValueError("拒绝发布时必须填写原因")
        return self


class MenuReleaseResponse(BaseModel):
    """定义菜单发布操作的稳定响应结构。"""

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
    """定义菜单发布快照菜单条目的协议字段和序列化边界。"""

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
    """定义菜单发布快照角色菜单条目的协议字段和序列化边界。"""

    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    menu_id: UUID
    visible: bool


class MenuReleaseSnapshotApiBindingEntry(BaseModel):
    """定义菜单发布快照API绑定条目的协议字段和序列化边界。"""

    model_config = ConfigDict(extra="forbid")

    menu_id: UUID
    api_resource_id: UUID
    action_type: Literal["query", "mutation", "publish", "approve"]


class MenuReleaseSnapshotResponse(BaseModel):
    """定义菜单发布快照操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    registry_version: int = Field(ge=1)
    workspace_id: UUID
    menu_version: int = Field(ge=1)
    menus: list[MenuReleaseSnapshotMenuEntry]
    role_menus: list[MenuReleaseSnapshotRoleMenuEntry]
    menu_api_bindings: list[MenuReleaseSnapshotApiBindingEntry]


class MenuReleaseListResponse(BaseModel):
    """定义菜单发布列表操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    items: list[MenuReleaseResponse]


class CurrentMenuReleaseResponse(BaseModel):
    """定义当前菜单发布操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    item: MenuReleaseResponse | None
    snapshot: MenuReleaseSnapshotResponse | None
