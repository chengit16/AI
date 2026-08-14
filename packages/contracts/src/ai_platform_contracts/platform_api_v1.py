"""由 scripts/generate_contract_types.py 自动生成。请勿手工修改。"""

from __future__ import annotations

import typing


class AssignMemberOrganizationRequest(typing.TypedDict):
    department_ids: list[str]
    position_ids: list[str]
    primary_department_id: typing.NotRequired[str | None]


class AuthenticationContextResponse(typing.TypedDict):
    actor_id: str
    authentication_method: str
    credential_scopes: list[str] | None
    request_id: str
    trace_id: str
    user_id: str | None
    workspace_id: str


class Body_uploadKnowledgeDocument(typing.TypedDict):
    department_ids: typing.NotRequired[list[str] | None]
    file: str
    permission_labels: typing.NotRequired[list[str] | None]
    security_level: typing.NotRequired[
        typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] | None
    ]
    title: str
    visibility: typing.NotRequired[typing.Literal["private", "workspace", "departments"] | None]


class Body_uploadKnowledgeDocumentVersion(typing.TypedDict):
    file: str


class CreateDepartmentRequest(typing.TypedDict):
    name: str
    parent_department_id: typing.NotRequired[str | None]


class CreateDocumentRequest(typing.TypedDict):
    captured_at: typing.NotRequired[str | None]
    department_ids: typing.NotRequired[list[str] | None]
    external_source_id: typing.NotRequired[str | None]
    original_object_key: typing.NotRequired[str | None]
    permission_labels: typing.NotRequired[list[str]]
    security_level: typing.NotRequired[
        typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] | None
    ]
    source_kind: typing.Literal["manual", "upload", "web", "data_source"]
    source_name: str
    source_path: typing.NotRequired[str | None]
    source_url: typing.NotRequired[str | None]
    title: str
    visibility: typing.NotRequired[typing.Literal["private", "workspace", "departments"] | None]


class CreateDocumentVersionRequest(typing.TypedDict):
    captured_at: typing.NotRequired[str | None]
    external_source_id: typing.NotRequired[str | None]
    original_object_key: typing.NotRequired[str | None]
    source_kind: typing.Literal["manual", "upload", "web", "data_source"]
    source_name: str
    source_path: typing.NotRequired[str | None]
    source_url: typing.NotRequired[str | None]


class CreateEnterpriseWorkspaceRequest(typing.TypedDict):
    name: str


class CreateKnowledgeBaseRequest(typing.TypedDict):
    default_security_level: typing.NotRequired[
        typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    ]
    default_visibility: typing.NotRequired[typing.Literal["private", "workspace", "departments"]]
    department_ids: typing.NotRequired[list[str]]
    description: typing.NotRequired[str | None]
    name: str


class CreateModelProviderRequest(typing.TypedDict):
    adapter_kind: typing.NotRequired[str]
    api_key: str
    base_url: str
    declared_capabilities: list[
        typing.Literal["generation", "streaming", "tools", "structured_output"]
    ]
    display_name: str
    location: typing.NotRequired[typing.Literal["external", "private"]]
    probe_model_id: str
    provider_key: str


class CreatePositionRequest(typing.TypedDict):
    department_id: str
    name: str


class CreateRoleBindingRequest(typing.TypedDict):
    account_id: typing.NotRequired[str | None]
    department_id: typing.NotRequired[str | None]
    role_id: str
    scope_type: typing.Literal["workspace", "department", "member"]


class CreateRoleRequest(typing.TypedDict):
    name: str
    role_key: str


class CurrentMenuReleaseResponse(typing.TypedDict):
    item: MenuReleaseResponse | None
    snapshot: MenuReleaseSnapshotResponse | None


class DepartmentListResponse(typing.TypedDict):
    items: list[DepartmentResponse]


class DepartmentResponse(typing.TypedDict):
    department_id: str
    depth: int
    effective_active: bool
    name: str
    parent_department_id: str | None
    status: typing.Literal["active", "disabled"]
    version: int


class DocumentCreatedResponse(typing.TypedDict):
    document: DocumentResponse
    document_version: DocumentVersionResponse
    source: DocumentSourceResponse


class DocumentResponse(typing.TypedDict):
    created_at: str
    created_by_account_id: str
    deleted_at: str | None
    department_ids: list[str]
    document_id: str
    knowledge_base_id: str
    permission_labels: list[str]
    security_level: typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    status: typing.Literal["active", "deleted"]
    title: str
    updated_at: str
    version: int
    visibility: typing.Literal["private", "workspace", "departments"]
    workspace_id: str


class DocumentSourceResponse(typing.TypedDict):
    captured_at: str | None
    created_at: str
    source_id: str
    source_kind: typing.Literal["manual", "upload", "web", "data_source"]
    source_name: str


class DocumentUploadResponse(typing.TypedDict):
    document: DocumentResponse
    document_version: DocumentVersionResponse
    source: DocumentSourceResponse
    upload: UploadMetadataResponse


class DocumentVersionCreatedResponse(typing.TypedDict):
    document_version: DocumentVersionResponse
    source: DocumentSourceResponse


class DocumentVersionResponse(typing.TypedDict):
    content_hash: str | None
    created_at: str
    created_by_account_id: str
    document_id: str
    document_version_id: str
    published_at: str | None
    record_version: int
    status: typing.Literal["draft", "ready", "published", "superseded"]
    version_number: int
    workspace_id: str


class DocumentVersionUploadResponse(typing.TypedDict):
    document_version: DocumentVersionResponse
    source: DocumentSourceResponse
    upload: UploadMetadataResponse


class EffectiveRoleResponse(typing.TypedDict):
    name: str
    role_id: str
    role_key: str
    sources: list[EffectiveRoleSourceResponse]


class EffectiveRoleSetResponse(typing.TypedDict):
    account_id: str
    membership_id: str
    role_version: int
    roles: list[EffectiveRoleResponse]


class EffectiveRoleSourceResponse(typing.TypedDict):
    scope_id: str
    scope_type: typing.Literal["workspace", "department", "member"]


class EntitlementResponse(typing.TypedDict):
    entitlement_version: int
    open_api_allowed: bool
    open_api_enabled: bool
    plan_code: str
    public_publish_allowed: bool
    quotas: list[QuotaResponse]
    workspace_id: str
    workspace_status: typing.Literal["active", "suspended", "archived"]


class ErrorResponse(typing.TypedDict):
    code: str
    message: str
    request_id: str
    retryable: bool
    trace_id: str


class HealthResponse(typing.TypedDict):
    checks: dict[str, typing.Literal["ok", "degraded"]]
    environment: str
    service: str
    status: typing.Literal["ok", "degraded"]
    version: str


class InviteWorkspaceMemberRequest(typing.TypedDict):
    login_name: str


class KnowledgeBaseResponse(typing.TypedDict):
    created_at: str
    created_by_account_id: str
    default_security_level: typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    default_visibility: typing.Literal["private", "workspace", "departments"]
    deleted_at: str | None
    department_ids: list[str]
    description: str | None
    knowledge_base_id: str
    name: str
    status: typing.Literal["active", "deleted"]
    updated_at: str
    version: int
    workspace_id: str


class LoginRequest(typing.TypedDict):
    login_name: str
    password: str


class LoginResponse(typing.TypedDict):
    account_id: str
    csrf_token: str
    personal_workspace_id: typing.NotRequired[str | None]


class LogoutResponse(typing.TypedDict):
    logged_out: bool


class MarkDocumentVersionReadyRequest(typing.TypedDict):
    content_hash: str


class MemberOrganizationResponse(typing.TypedDict):
    account_id: str
    department_ids: list[str]
    membership_version: int
    position_ids: list[str]
    primary_department_id: str | None


class MenuReleaseDecisionRequest(typing.TypedDict):
    approved: bool
    reason: typing.NotRequired[str | None]


class MenuReleaseListResponse(typing.TypedDict):
    items: list[MenuReleaseResponse]


class MenuReleaseResponse(typing.TypedDict):
    created_at: str
    created_by_account_id: str
    decided_at: str | None
    decided_by_account_id: str | None
    menu_api_binding_count: int
    menu_count: int
    menu_version: int
    published_at: str | None
    registry_version: int
    rejection_reason: str | None
    release_id: str
    release_kind: typing.Literal["standard", "rollback"]
    release_number: int
    role_menu_count: int
    snapshot_digest: str
    snapshot_schema_version: int
    source_release_id: str | None
    status: typing.Literal["draft", "validated", "approved", "rejected", "published"]
    validated_at: str | None
    validation_errors: list[str]
    version: int
    workspace_id: str


class MenuReleaseSnapshotApiBindingEntry(typing.TypedDict):
    action_type: typing.Literal["query", "mutation", "publish", "approve"]
    api_resource_id: str
    menu_id: str


class MenuReleaseSnapshotMenuEntry(typing.TypedDict):
    icon_key: str | None
    menu_id: str
    menu_key: str
    menu_type: typing.Literal["directory", "page", "action"]
    name: str
    page_resource_id: str | None
    parent_menu_id: str | None
    permission_code: str | None
    sort_order: int
    source: typing.Literal["system", "workspace"]
    status: typing.Literal["active", "disabled"]
    visible: bool


class MenuReleaseSnapshotResponse(typing.TypedDict):
    menu_api_bindings: list[MenuReleaseSnapshotApiBindingEntry]
    menu_version: int
    menus: list[MenuReleaseSnapshotMenuEntry]
    registry_version: int
    role_menus: list[MenuReleaseSnapshotRoleMenuEntry]
    schema_version: int
    workspace_id: str


class MenuReleaseSnapshotRoleMenuEntry(typing.TypedDict):
    menu_id: str
    role_id: str
    visible: bool


class ModelProviderConfigurationListResponse(typing.TypedDict):
    items: list[ModelProviderConfigurationResponse]


class ModelProviderConfigurationResponse(typing.TypedDict):
    adapter_kind: str
    base_url: str
    created_at: str
    declared_capabilities: list[
        typing.Literal["generation", "streaming", "tools", "structured_output"]
    ]
    display_name: str
    last_probe_error_code: str | None
    last_probed_at: str | None
    location: typing.Literal["external", "private"]
    max_security_level: typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    policy_review_status: typing.Literal["pending", "approved", "rejected"]
    policy_reviewed_at: str | None
    policy_url: str | None
    policy_version: str | None
    probe_model_id: str
    probe_status: typing.Literal["not_run", "passed", "failed"]
    probed_capabilities: list[
        typing.Literal["generation", "streaming", "tools", "structured_output"]
    ]
    provider_id: str
    provider_key: str
    retention_days: int | None
    status: typing.Literal["draft", "active", "disabled"]
    training_usage_allowed: bool
    updated_at: str
    version: int


class MoveDepartmentRequest(typing.TypedDict):
    parent_department_id: typing.NotRequired[str | None]


class OpenApiFeatureRequest(typing.TypedDict):
    enabled: bool


class OrganizationStatusRequest(typing.TypedDict):
    active: bool


class PositionListResponse(typing.TypedDict):
    items: list[PositionResponse]


class PositionResponse(typing.TypedDict):
    department_id: str
    effective_active: bool
    name: str
    position_id: str
    status: typing.Literal["active", "disabled"]
    version: int


class QuotaResponse(typing.TypedDict):
    limit_value: int
    metric: typing.Literal[
        "members", "storage_bytes", "knowledge_bases", "published_agents", "questions_monthly"
    ]
    period_key: str
    remaining_value: int
    used_value: int


class RegistrationRequest(typing.TypedDict):
    display_name: str
    login_name: str
    password: str


class RegistrationResponse(typing.TypedDict):
    account_id: str
    personal_workspace_id: str


class ReplaceRoleMenuVisibilityRequest(typing.TypedDict):
    items: list[RoleMenuVisibilityEntry]


class ReplaceRolePermissionsRequest(typing.TypedDict):
    items: list[RolePermissionEntry]


class ReplaceWorkspaceMenuConfigurationRequest(typing.TypedDict):
    items: list[WorkspaceMenuOverrideEntry]


class ReviewModelProviderDataPolicyRequest(typing.TypedDict):
    approved: bool
    max_security_level: typing.NotRequired[
        typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    ]
    policy_url: typing.NotRequired[str | None]
    policy_version: typing.NotRequired[str | None]
    retention_days: typing.NotRequired[int | None]
    training_usage_allowed: typing.NotRequired[bool]


class RoleBindingResponse(typing.TypedDict):
    binding_id: str
    department_id: str | None
    membership_id: str | None
    role_id: str
    scope_type: typing.Literal["workspace", "department", "member"]
    status: typing.Literal["active", "revoked"]
    version: int


class RoleListResponse(typing.TypedDict):
    items: list[RoleResponse]


class RoleMenuVisibilityEntry(typing.TypedDict):
    menu_id: str
    visible: bool


class RoleMenuVisibilityResponse(typing.TypedDict):
    items: list[RoleMenuVisibilityEntry]
    role_id: str


class RolePermissionEntry(typing.TypedDict):
    department_ids: typing.NotRequired[list[str]]
    field_mask: typing.NotRequired[list[str]]
    maximum_security_level: typing.NotRequired[
        typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    ]
    permission_code: str
    resource_ids: typing.NotRequired[list[str]]
    scope_type: typing.Literal["workspace", "department_tree", "self", "resource"]


class RolePermissionListResponse(typing.TypedDict):
    items: list[RolePermissionEntry]


class RoleResponse(typing.TypedDict):
    name: str
    role_id: str
    role_key: str
    status: typing.Literal["active", "disabled"]
    system_managed: bool
    version: int


class RoleStatusRequest(typing.TypedDict):
    active: bool


class RotateModelProviderCredentialRequest(typing.TypedDict):
    api_key: str


class UploadMetadataResponse(typing.TypedDict):
    content_hash: str
    media_type: str
    scan_status: typing.NotRequired[str]
    size_bytes: int


class WorkspaceInvitationResponse(typing.TypedDict):
    expires_at: str
    invitation_id: str
    status: typing.Literal["pending", "accepted", "cancelled", "expired"]
    workspace_id: str


class WorkspaceListResponse(typing.TypedDict):
    items: list[WorkspaceSummaryResponse]


class WorkspaceMemberListResponse(typing.TypedDict):
    items: list[WorkspaceMemberResponse | WorkspaceMemberProjectionResponse]


class WorkspaceMemberProjectionResponse(typing.TypedDict):
    account_id: typing.NotRequired[str | None]
    display_name: typing.NotRequired[str | None]
    membership_type: typing.NotRequired[typing.Literal["owner", "member"] | None]
    status: typing.NotRequired[typing.Literal["active", "disabled", "left"] | None]


class WorkspaceMemberResponse(typing.TypedDict):
    account_id: str
    display_name: str
    membership_type: typing.Literal["owner", "member"]
    status: typing.Literal["active", "disabled", "left"]


class WorkspaceMembershipResponse(typing.TypedDict):
    account_id: str
    membership_type: typing.Literal["owner", "member"]
    status: typing.Literal["active", "disabled", "left"]


class WorkspaceMenuConfigurationResponse(typing.TypedDict):
    items: list[WorkspaceMenuOverrideEntry]
    menu_version: int
    workspace_id: str


class WorkspaceMenuOverrideEntry(typing.TypedDict):
    icon_key: typing.NotRequired[str | None]
    menu_id: str
    name: str
    parent_menu_id: typing.NotRequired[str | None]
    sort_order: int
    version: typing.NotRequired[int]
    visible: bool


class WorkspaceSummaryResponse(typing.TypedDict):
    membership_status: typing.Literal["active", "disabled", "left"]
    membership_type: typing.Literal["owner", "member"]
    name: str
    status: typing.Literal["active", "suspended", "archived"]
    workspace_id: str
    workspace_type: typing.Literal["personal", "enterprise"]
