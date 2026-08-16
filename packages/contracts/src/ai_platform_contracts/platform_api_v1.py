"""由 scripts/generate_contract_types.py 自动生成。请勿手工修改。"""

from __future__ import annotations

import typing


class AgentApprovalResponse(typing.TypedDict):
    approval_instance_id: str
    completed_at: str | None
    personal_owner_confirmation: bool
    status: typing.Literal["pending", "approved", "rejected", "withdrawn"]


class AgentCandidateControlResponse(typing.TypedDict):
    approval: AgentApprovalResponse | None
    candidate: AgentCandidateResponse
    evaluation: AgentEvaluationResponse | None


class AgentCandidateListResponse(typing.TypedDict):
    items: list[AgentCandidateControlResponse]


class AgentCandidateResponse(typing.TypedDict):
    agent_id: str
    candidate_hash: str
    candidate_id: str
    config_hash: str
    created_at: str
    draft_revision: int
    status: str
    updated_at: str
    version: int


class AgentDetailResponse(typing.TypedDict):
    agent: AgentResponse
    draft: AgentDraftResponse


class AgentDraftResponse(typing.TypedDict):
    agent_id: str
    config_hash: str
    configuration: dict[str, object]
    draft_id: str
    revision: int
    status: str
    updated_at: str


class AgentEvaluationCheckResponse(typing.TypedDict):
    case_count: int
    check_code: str
    passed_count: int
    score_bps: int
    status: typing.Literal["passed", "failed"]


class AgentEvaluationResponse(typing.TypedDict):
    candidate_id: str
    checks: list[AgentEvaluationCheckResponse]
    completed_at: str
    evaluation_run_id: str
    evaluator_version: str
    evidence_level: str
    failed_cases: int
    passed_cases: int
    result_hash: str
    skipped_cases: int
    status: typing.Literal["passed", "failed"]
    timeout_cases: int
    total_cases: int


class AgentListResponse(typing.TypedDict):
    items: list[AgentDetailResponse]


class AgentOperationsReportResponse(typing.TypedDict):
    ai_quality_status: str
    alerts: list[OperationsAlertResponse]
    canary_percent: int
    comparison: ReleaseOperationsMetricsResponse | None
    minimum_feedback_samples: int
    minimum_terminal_samples: int
    online_llm_grading: bool
    primary: ReleaseOperationsMetricsResponse
    promotion: PromotionDecisionResponse
    route_id: str
    route_mode: str
    route_version: int
    service_id: str
    service_name: str
    service_status: str
    window_ended_at: str
    window_started_at: str


class AgentReleaseListResponse(typing.TypedDict):
    items: list[AgentReleaseResponse]


class AgentReleaseResponse(typing.TypedDict):
    agent_id: str
    candidate_id: str | None
    config_hash: str
    release_id: str
    released_at: str
    released_by_account_id: str
    snapshot_hash: str | None
    source_draft_revision: int | None
    version: int


class AgentResponse(typing.TypedDict):
    agent_id: str
    agent_key: str
    created_at: str
    created_by_account_id: str
    description: str | None
    name: str
    status: typing.Literal["active", "archived"]
    updated_at: str
    version: int
    workspace_id: str


class AiRuntimeConfigListResponse(typing.TypedDict):
    items: list[AiRuntimeConfigResponse]


class AiRuntimeConfigPublicationResponse(typing.TypedDict):
    generation: int
    published_at: str
    published_by_account_id: str
    runtime_config_version_id: str


class AiRuntimeConfigResponse(typing.TypedDict):
    components: RuntimeComponentVersionsSchema
    content_hash: str
    created_at: str
    created_by_account_id: str
    display_name: str
    policy: GatewayPolicySchema
    routes: list[RuntimeRouteResponse]
    runtime_config_version_id: str
    system_prompt_hash: str
    system_prompt_template: str
    version_number: int


class ApprovalActionRequest(typing.TypedDict):
    idempotency_key: str
    reason_code: typing.NotRequired[str | None]


class ApprovalApproverSourceDocument(typing.TypedDict):
    levels_up: typing.NotRequired[int | None]
    reference_ids: list[str]
    source_type: typing.Literal["accounts", "roles", "department_managers", "upper_managers"]


class ApprovalAssignmentResponse(typing.TypedDict):
    approval_assignment_id: str
    approval_level_id: str
    approver_account_id: str
    created_at: str
    decided_at: str | None
    status: typing.Literal["waiting", "pending", "approved", "rejected", "transferred", "cancelled"]
    transferred_to_account_id: str | None
    version: int


class ApprovalChainResponse(typing.TypedDict):
    approval_policy_id: str | None
    approval_policy_version_id: str | None
    chain_digest: str
    levels: list[ResolvedApprovalLevelResponse]
    personal_owner_confirmation: bool
    workspace_id: str


class ApprovalCommandResponse(typing.TypedDict):
    instance: ApprovalInstanceResponse
    replayed: bool


class ApprovalFieldConditionDocument(typing.TypedDict):
    expected: typing.NotRequired[object | None]
    field_path: str
    operator: typing.Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists"]


class ApprovalInstanceListResponse(typing.TypedDict):
    items: list[ApprovalInstanceResponse]


class ApprovalInstanceResponse(typing.TypedDict):
    approval_instance_id: str
    approval_policy_id: str | None
    approval_policy_version_id: str | None
    assignments: list[ApprovalAssignmentResponse]
    chain_digest: str
    completed_at: str | None
    created_at: str
    current_sequence_no: int
    levels: list[ApprovalLevelResponse]
    operation: str
    personal_owner_confirmation: bool
    requester_account_id: str
    resource_id: str | None
    resource_type: str
    status: typing.Literal["pending", "approved", "rejected", "withdrawn"]
    subject_digest: str
    updated_at: str
    version: int
    workflow_run_id: str | None
    workflow_step_id: str | None
    workspace_id: str


class ApprovalLevelDocument(typing.TypedDict):
    fallback_sources: typing.NotRequired[list[ApprovalApproverSourceDocument]]
    mode: typing.Literal["any", "all"]
    reminder_after_minutes: typing.NotRequired[int]
    sequence_no: int
    sources: list[ApprovalApproverSourceDocument]
    timeout_action: typing.NotRequired[typing.Literal["escalate", "transfer", "reject", "wait"]]
    timeout_after_minutes: typing.NotRequired[int]


class ApprovalLevelResponse(typing.TypedDict):
    activated_at: str | None
    approval_level_id: str
    completed_at: str | None
    fallback_activated: bool
    fallback_approver_account_ids: list[str]
    mode: typing.Literal["any", "all"]
    reminded_at: str | None
    reminder_after_minutes: int
    reminder_at: str | None
    sequence_no: int
    status: typing.Literal["waiting", "active", "approved", "rejected", "withdrawn"]
    timeout_action: typing.Literal["escalate", "transfer", "reject", "wait"]
    timeout_after_minutes: int
    timeout_at: str | None
    version: int


class ApprovalPolicyDefinitionDocument(typing.TypedDict):
    allow_self_approval: typing.NotRequired[bool]
    department_ids: typing.NotRequired[list[str]]
    field_conditions: typing.NotRequired[list[ApprovalFieldConditionDocument]]
    levels: list[ApprovalLevelDocument]
    operation: str
    priority: int
    resource_type: str
    risk_levels: typing.NotRequired[list[typing.Literal["normal", "high", "critical"]]]
    security_levels: typing.NotRequired[
        list[typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]]
    ]


class ApprovalPolicyDetailResponse(typing.TypedDict):
    policy: ApprovalPolicyResponse
    version: ApprovalPolicyVersionResponse


class ApprovalPolicyListResponse(typing.TypedDict):
    items: list[ApprovalPolicyResponse]


class ApprovalPolicyResponse(typing.TypedDict):
    approval_policy_id: str
    created_at: str
    created_by_account_id: str
    current_version_id: str
    name: str
    status: typing.Literal["active", "disabled"]
    updated_at: str
    version: int
    workspace_id: str


class ApprovalPolicyVersionResponse(typing.TypedDict):
    approval_policy_id: str
    approval_policy_version_id: str
    created_at: str
    created_by_account_id: str
    definition: ApprovalPolicyDefinitionDocument
    definition_digest: str
    version_number: int
    workspace_id: str


class ArchiveAgentRequest(typing.TypedDict):
    expected_version: int


class AssignMemberOrganizationRequest(typing.TypedDict):
    department_ids: list[str]
    position_ids: list[str]
    primary_department_id: typing.NotRequired[str | None]


class AssistantRunListResponse(typing.TypedDict):
    items: list[AssistantRunResponse]


class AssistantRunResponse(typing.TypedDict):
    agent_release_id: str
    assistant_message_id: str | None
    completed_at: str | None
    conversation_id: str
    created_at: str
    error_code: str | None
    run_id: str
    runtime_config_version_id: str
    service_id: typing.NotRequired[str | None]
    service_route_id: typing.NotRequired[str | None]
    service_route_version: typing.NotRequired[int | None]
    status: typing.Literal["queued", "running", "completed", "failed", "cancelled"]
    trace_id: str
    updated_at: str
    user_message_id: str
    workspace_id: str


class AssistantSourceListResponse(typing.TypedDict):
    items: list[AssistantSourceResponse]


class AssistantSourceResponse(typing.TypedDict):
    chunk_id: str
    conflict_detected: bool
    content_hash: str
    document_id: str
    document_title: str
    document_version_id: str
    quote: str
    rank: int
    source_kind: typing.Literal["manual", "upload", "web", "data_source"]
    source_name: str
    source_position: dict[str, object]


class AuditRecordPageResponse(typing.TypedDict):
    items: list[AuditRecordResponse]
    next_cursor: str | None


class AuditRecordResponse(typing.TypedDict):
    action: str
    actor_id: str
    audit_id: str
    occurred_at: str
    outcome: typing.Literal["succeeded", "denied", "failed"]
    permission_code: str | None
    policy_decision_id: str | None
    policy_version: int | None
    request_id: str
    resource_id: str
    resource_type: str
    trace_id: str
    user_id: str | None
    workspace_id: str


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


class ConversationListResponse(typing.TypedDict):
    items: list[ConversationResponse]


class ConversationResponse(typing.TypedDict):
    conversation_id: str
    created_at: str
    created_by_account_id: str
    status: typing.Literal["active", "archived"]
    title: str | None
    updated_at: str
    version: int
    workspace_id: str


class CreateAgentRequest(typing.TypedDict):
    configuration: typing.NotRequired[dict[str, object] | None]
    description: typing.NotRequired[str | None]
    name: str
    use_starter_configuration: typing.NotRequired[bool]


class CreateAiRuntimeConfigRequest(typing.TypedDict):
    components: RuntimeComponentVersionsSchema
    display_name: str
    policy: GatewayPolicySchema
    routes: list[RuntimeRouteRequest]
    system_prompt_template: str


class CreateApprovalPolicyRequest(typing.TypedDict):
    definition: ApprovalPolicyDefinitionDocument
    name: str


class CreateConversationRequest(typing.TypedDict):
    title: typing.NotRequired[str | None]


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


class CreateMessagePartRequest(typing.TypedDict):
    text: str
    type: str


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


class CreateServiceRequest(typing.TypedDict):
    allowed_account_ids: typing.NotRequired[list[str]]
    allowed_department_ids: typing.NotRequired[list[str]]
    name: str
    release_id: str
    service_type: typing.Literal["custom_knowledge_agent", "scenario_application", "open_api"]
    visibility: typing.Literal["workspace", "restricted"]


class CreateToolRunRequest(typing.TypedDict):
    agent_release_id: str
    max_attempts_per_step: typing.NotRequired[int]
    max_execution_seconds: typing.NotRequired[int]
    service_id: str
    tool_calls: list[ToolIntentRequest]


class CreateUserMessageRequest(typing.TypedDict):
    parts: list[CreateMessagePartRequest]


class CreateWorkflowRequest(typing.TypedDict):
    description: typing.NotRequired[str | None]
    graph: WorkflowGraphDocument
    name: str


class CreateWorkflowRunRequest(typing.TypedDict):
    input_payload: typing.NotRequired[dict[str, object]]
    workflow_version_id: str


class CurrentAiRuntimeConfigResponse(typing.TypedDict):
    item: AiRuntimeConfigResponse | None


class CurrentMenuReleaseResponse(typing.TypedDict):
    item: MenuReleaseResponse | None
    snapshot: MenuReleaseSnapshotResponse | None


class CurrentMessageFeedbackResponse(typing.TypedDict):
    item: MessageFeedbackResponse | None


class DeletionCertificateResponse(typing.TypedDict):
    certificate_id: str
    completed_at: str
    deleted_cache_key_count: int
    deleted_object_count: int
    deleted_table_counts: dict[str, int]
    registry_version: int
    result_sha256: str


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


class DueApprovalResponse(typing.TypedDict):
    items: list[ApprovalCommandResponse]


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


class GatewayPolicySchema(typing.TypedDict):
    attempt_timeout_ms: int
    circuit_failure_threshold: int
    circuit_recovery_ms: int
    max_attempts_per_route: int
    max_estimated_cost_microunits: int
    max_output_tokens: int
    max_prompt_characters: int
    max_response_characters: int
    rule_degradation_message: typing.NotRequired[str | None]
    total_timeout_ms: int


class HealthCheckDetail(typing.TypedDict):
    checked_at: str
    critical: bool
    latency_ms: float
    reason_code: typing.NotRequired[str | None]
    status: typing.Literal["ok", "degraded"]


class HealthResponse(typing.TypedDict):
    checked_at: typing.NotRequired[str | None]
    checks: dict[str, typing.Literal["ok", "degraded"]]
    details: typing.NotRequired[dict[str, HealthCheckDetail] | None]
    environment: str
    service: str
    status: typing.Literal["ok", "degraded"]
    version: str


class IndexMaintenanceCommandBody(typing.TypedDict):
    confirmation: str
    reason_code: str


class IndexMaintenanceRequestListResponse(typing.TypedDict):
    items: list[IndexMaintenanceRequestResponse]


class IndexMaintenanceRequestResponse(typing.TypedDict):
    attempt_count: int
    command: typing.Literal["inspection", "full_rebuild", "cleanup"]
    completed_at: str | None
    created_at: str
    last_error_code: str | None
    maintenance_request_id: str
    reason_code: str
    requested_by_actor_id: str
    status: typing.Literal["pending", "running", "retry_wait", "completed", "dead_letter"]
    updated_at: str
    workspace_id: str


class IndexMaintenanceRunListResponse(typing.TypedDict):
    items: list[IndexMaintenanceRunResponse]


class IndexMaintenanceRunResponse(typing.TypedDict):
    cleaned_chunk_count: int
    completed_at: str
    inconsistency_count: int
    maintenance_run_id: str
    rebuild_queued_count: int
    repaired_count: int
    requested_by_actor_id: str | None
    result_digest: str
    run_kind: typing.Literal["inspection", "full_rebuild", "cleanup"]
    scanned_document_count: int
    started_at: str


class IngestionJobListResponse(typing.TypedDict):
    items: list[IngestionJobResponse]


class IngestionJobResponse(typing.TypedDict):
    attempt_count: int
    available_at: str
    block_count: int | None
    can_cancel: typing.NotRequired[bool]
    can_retry_manually: bool
    cancelled_at: typing.NotRequired[str | None]
    completed_at: str | None
    created_at: str
    document_id: str
    document_version_id: str
    error_code: str | None
    error_message: str | None
    failure_stage: typing.Literal["source", "parse", "ocr", "artifact", "worker"] | None
    ingestion_job_id: str
    knowledge_base_id: str
    last_retried_at: str | None
    manual_retry_count: int
    max_attempts: int
    ocr_used: bool | None
    page_count: int | None
    parsed_content_hash: str | None
    parser_name: str | None
    source_id: str
    source_media_type: str
    source_name: str
    started_at: str | None
    status: typing.Literal[
        "queued", "running", "retry_wait", "succeeded", "failed", "cancelled", "timed_out"
    ]
    updated_at: str


class IntegrationInspectionResponse(typing.TypedDict):
    checked_at: str
    consumer_receipt_count: int
    duplicate_delivery_count: int
    expired_claim_count: int
    idempotency_issue_count: int
    incompatible_schema_count: int
    oldest_pending_age_seconds: float
    replay_request_count: int
    status_counts: list[OutboxStatusCountResponse]


class InviteWorkspaceMemberRequest(typing.TypedDict):
    login_name: str


class InvokePublishedServiceRequest(typing.TypedDict):
    parts: list[CreateMessagePartRequest]


class KnowledgeBaseListResponse(typing.TypedDict):
    items: list[KnowledgeBaseSummaryResponse]


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


class KnowledgeBaseSummaryResponse(typing.TypedDict):
    default_security_level: typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    default_visibility: typing.Literal["private", "workspace", "departments"]
    description: str | None
    knowledge_base_id: str
    name: str
    updated_at: str


class KnowledgeDocumentListResponse(typing.TypedDict):
    items: list[KnowledgeDocumentSummaryResponse]


class KnowledgeDocumentSummaryResponse(typing.TypedDict):
    current_document_version_id: str | None
    document_id: str
    latest_version: DocumentVersionResponse
    security_level: typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    source_id: str
    source_kind: typing.Literal["manual", "upload", "web", "data_source"]
    source_name: str
    title: str
    updated_at: str
    visibility: typing.Literal["private", "workspace", "departments"]


class LifecycleExportResponse(typing.TypedDict):
    bundle_sha256: str | None
    bundle_size_bytes: int | None
    completed_at: str | None
    created_at: str
    error_code: str | None
    export_id: str
    object_count: int | None
    object_key: str | None
    object_manifest_sha256: str | None
    status: str
    table_count: int | None


class LifecycleOperationListResponse(typing.TypedDict):
    items: list[LifecycleOperationResponse]


class LifecycleOperationResponse(typing.TypedDict):
    completed_at: str | None
    created_at: str
    error_code: str | None
    operation_id: str
    operation_kind: typing.Literal["export", "purge", "retention"]
    result_count: int | None
    status: str


class LifecyclePurgeBody(typing.TypedDict):
    confirmed_workspace_name: str
    reason_code: str


class LifecyclePurgeResponse(typing.TypedDict):
    cache_cleared: bool
    certificate: DeletionCertificateResponse
    completed_at: str | None
    database_cleared: bool
    objects_cleared: bool
    purge_request_id: str
    status: str


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


class MessageFeedbackResponse(typing.TypedDict):
    comment: str | None
    conversation_id: str
    created_at: str
    feedback_id: str
    issue_codes: list[
        typing.Literal["incorrect", "missing_source", "source_mismatch", "unsafe", "other"]
    ]
    message_id: str
    rating: typing.Literal["helpful", "unhelpful"]
    run_id: str
    updated_at: str
    version: int
    workspace_id: str


class MessageListResponse(typing.TypedDict):
    items: list[MessageResponse]


class MessagePartResponse(typing.TypedDict):
    part_id: str
    sequence_no: int
    text: str
    type: str


class MessageResponse(typing.TypedDict):
    conversation_id: str
    created_at: str
    created_by_account_id: str
    message_id: str
    parts: list[MessagePartResponse]
    role: typing.Literal["system", "user", "assistant", "tool"]
    status: typing.Literal["streaming", "completed", "failed"]
    updated_at: str
    version: int
    workspace_id: str


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


class OperationsAlertResponse(typing.TypedDict):
    blocks_promotion: bool
    code: str
    metric: str
    observed_value: int
    release_role: typing.Literal["primary", "canary", "previous"]
    severity: typing.Literal["warning", "critical"]
    threshold_value: int


class OperationsIngestionJobListResponse(typing.TypedDict):
    items: list[OperationsIngestionJobResponse]


class OperationsIngestionJobResponse(typing.TypedDict):
    attempt_count: int
    created_at: str
    document_id: str
    error_code: str | None
    ingestion_job_id: str
    knowledge_base_id: str
    manual_retry_count: int
    max_attempts: int
    processing_lane: typing.Literal["parsing", "ocr"]
    source_name: str
    status: str
    updated_at: str


class OperationsOverviewResponse(typing.TypedDict):
    checked_at: str
    index_requests: list[StatusCountResponse]
    index_versions: list[StatusCountResponse]
    ingestion: list[StatusCountResponse]
    lifecycle_active_count: int
    outbox: list[StatusCountResponse]


class OrganizationStatusRequest(typing.TypedDict):
    active: bool


class OutboxEventPageResponse(typing.TypedDict):
    items: list[OutboxEventResponse]
    next_cursor: str | None


class OutboxEventResponse(typing.TypedDict):
    actor_id: str | None
    aggregate_id: str
    aggregate_version: int
    attempt_count: int
    available_at: str
    claim_until: str | None
    event_id: str
    event_type: str
    last_error_code: str | None
    occurred_at: str
    published_at: str | None
    replay_count: int
    request_id: str | None
    schema_version: int
    status: typing.Literal["pending", "publishing", "published", "dead_letter"]
    trace_id: str
    user_id: str | None
    workspace_id: str


class OutboxReplayBody(typing.TypedDict):
    idempotency_key: str
    reason_code: str


class OutboxReplayResponse(typing.TypedDict):
    event_id: str
    idempotency_key: str
    reason_code: str
    replay_request_id: str
    request_id: str
    requested_at: str
    requested_by_actor_id: str
    requested_by_user_id: str | None
    source_attempt_count: int
    source_error_code: str | None
    source_published_at: str | None
    source_status: typing.Literal["published", "dead_letter"]
    trace_id: str
    workspace_id: str


class OutboxStatusCountResponse(typing.TypedDict):
    count: int
    status: typing.Literal["pending", "publishing", "published", "dead_letter"]


class PositionListResponse(typing.TypedDict):
    items: list[PositionResponse]


class PositionResponse(typing.TypedDict):
    department_id: str
    effective_active: bool
    name: str
    position_id: str
    status: typing.Literal["active", "disabled"]
    version: int


class PreviewApprovalChainRequest(typing.TypedDict):
    department_ids: typing.NotRequired[list[str]]
    fields: typing.NotRequired[dict[str, object]]
    operation: str
    resource_id: typing.NotRequired[str | None]
    resource_type: str
    risk_level: typing.Literal["normal", "high", "critical"]
    security_level: typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


class PromoteServiceRouteRequest(typing.TypedDict):
    expected_generation: int
    release_id: str


class PromotionDecisionResponse(typing.TypedDict):
    allowed: bool
    evidence_hash: str
    policy_version: str
    reason_codes: list[str]
    status: typing.Literal["passed", "blocked", "insufficient_data", "not_applicable"]


class PublishWorkflowRequest(typing.TypedDict):
    expected_revision: int


class PublishedServiceInvocationResponse(typing.TypedDict):
    event_stream_path: str
    input_message: MessageResponse
    output_message: MessageResponse | None
    run: AssistantRunResponse


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


class ReleaseOperationsMetricsResponse(typing.TypedDict):
    average_cost_microunits: int | None
    completed_count: int
    degradation_rate_bps: int | None
    error_rate_bps: int | None
    failed_count: int
    feedback_count: int
    feedback_quality_status: typing.Literal["measured", "not_run"]
    helpful_rate_bps: int | None
    latency_p95_ms: int | None
    max_cost_budget_microunits: int
    max_run_cost_microunits: int
    offline_evaluation_score_bps: int
    offline_evaluation_status: str
    release_id: str
    release_version: int
    role: typing.Literal["primary", "canary", "previous"]
    run_count: int
    success_rate_bps: int | None
    terminal_count: int
    total_cost_microunits: int


class ReplaceRoleMenuVisibilityRequest(typing.TypedDict):
    items: list[RoleMenuVisibilityEntry]


class ReplaceRolePermissionsRequest(typing.TypedDict):
    items: list[RolePermissionEntry]


class ReplaceWorkspaceMenuConfigurationRequest(typing.TypedDict):
    items: list[WorkspaceMenuOverrideEntry]


class RequestAgentReleaseRequest(typing.TypedDict):
    expected_revision: int


class ResolvedApprovalLevelResponse(typing.TypedDict):
    approver_account_ids: list[str]
    fallback_approver_account_ids: list[str]
    mode: typing.Literal["any", "all"]
    reminder_after_minutes: int
    sequence_no: int
    timeout_action: typing.Literal["escalate", "transfer", "reject", "wait"]
    timeout_after_minutes: int


class RetentionRunResponse(typing.TypedDict):
    completed_at: str | None
    created_at: str
    cutoffs: dict[str, str]
    deleted_table_counts: dict[str, int]
    result_sha256: str | None
    retention_run_id: str
    status: str


class ReviewModelProviderDataPolicyRequest(typing.TypedDict):
    approved: bool
    max_security_level: typing.NotRequired[
        typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    ]
    policy_url: typing.NotRequired[str | None]
    policy_version: typing.NotRequired[str | None]
    retention_days: typing.NotRequired[int | None]
    training_usage_allowed: typing.NotRequired[bool]


class ReviseApprovalPolicyRequest(typing.TypedDict):
    definition: ApprovalPolicyDefinitionDocument
    expected_version: int


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


class RollbackServiceRouteRequest(typing.TypedDict):
    expected_generation: int


class RotateModelProviderCredentialRequest(typing.TypedDict):
    api_key: str


class RuntimeComponentVersionsSchema(typing.TypedDict):
    chunking: str
    data_source_interface: str
    embedding: str
    index_schema: str
    multimodal_router_interface: str
    relevance_grader_interface: str
    reranker: str
    retrieval: str
    safety: str
    source_ranking: str


class RuntimeRouteRequest(typing.TypedDict):
    capabilities: list[typing.Literal["generation", "streaming", "tools", "structured_output"]]
    input_price_microunits_per_million_tokens: int
    model_id: str
    output_price_microunits_per_million_tokens: int
    priority: int
    provider_id: str


class RuntimeRouteResponse(typing.TypedDict):
    capabilities: list[typing.Literal["generation", "streaming", "tools", "structured_output"]]
    currency: str
    input_price_microunits_per_million_tokens: int
    location: typing.Literal["external", "private"]
    model_id: str
    output_price_microunits_per_million_tokens: int
    priority: int
    provider_configuration_version: int
    provider_id: str
    route_id: str


class ServiceAccessPolicyResponse(typing.TypedDict):
    access_policy_version_id: str
    allowed_account_ids: list[str]
    allowed_department_ids: list[str]
    policy_hash: str
    version: int
    visibility: typing.Literal["workspace", "restricted"]


class ServiceDeploymentResponse(typing.TypedDict):
    access_policy: ServiceAccessPolicyResponse
    publication: ServicePublicationResponse
    route: ServiceRouteResponse
    service: ServiceResponse


class ServiceListResponse(typing.TypedDict):
    items: list[ServiceDeploymentResponse]


class ServicePublicationResponse(typing.TypedDict):
    generation: int
    published_at: str
    route_id: str


class ServiceResponse(typing.TypedDict):
    agent_id: str
    name: str
    service_id: str
    service_key: str
    service_type: str
    status: str
    updated_at: str
    version: int


class ServiceRouteResponse(typing.TypedDict):
    canary_percent: int
    canary_release_id: str | None
    created_at: str
    previous_route_id: str | None
    primary_release_id: str
    route_hash: str
    route_id: str
    route_mode: typing.Literal["active", "canary", "rollback"]
    route_version: int


class StartApprovalInstanceRequest(typing.TypedDict):
    department_ids: typing.NotRequired[list[str]]
    fields: typing.NotRequired[dict[str, object]]
    idempotency_key: str
    operation: str
    resource_id: typing.NotRequired[str | None]
    resource_type: str
    risk_level: typing.Literal["normal", "high", "critical"]
    security_level: typing.Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


class StartServiceCanaryRequest(typing.TypedDict):
    canary_percent: int
    expected_generation: int
    release_id: str


class StatusCountResponse(typing.TypedDict):
    count: int
    status: str


class SubmitMessageFeedbackRequest(typing.TypedDict):
    comment: typing.NotRequired[str | None]
    issue_codes: typing.NotRequired[
        list[typing.Literal["incorrect", "missing_source", "source_mismatch", "unsafe", "other"]]
    ]
    rating: typing.Literal["helpful", "unhelpful"]


class ToolAttemptResponse(typing.TypedDict):
    attempt_id: str
    attempt_no: int
    call_state: str
    completed_at: str | None
    cost_microunits: int | None
    duration_ms: int | None
    error_code: str | None
    recovery_generation: int
    result_size_bytes: int | None
    result_status: str | None
    started_at: str
    state: str
    tool_call_id: str
    trigger: str


class ToolCatalogItemResponse(typing.TypedDict):
    access_mode: str
    credential_requirement: str
    description: str
    display_name: str
    input_schema: dict[str, object]
    permission_code: str
    retry_mode: str
    risk_level: str
    synthetic: bool
    timeout_seconds: int
    tool_id: str
    tool_key: str
    tool_version: int


class ToolCatalogResponse(typing.TypedDict):
    items: list[ToolCatalogItemResponse]


class ToolConfirmationActionRequest(typing.TypedDict):
    idempotency_key: str
    reason_code: typing.NotRequired[str | None]


class ToolConfirmationResponse(typing.TypedDict):
    approval_instance_id: str
    can_respond: bool
    confirmation_hash: str
    confirmation_id: str
    expires_at: str
    mode: typing.Literal["personal_owner", "enterprise_approval"]
    resolved_at: str | None
    risk_level: str
    state: str
    version: int


class ToolIntentRequest(typing.TypedDict):
    arguments: dict[str, object]
    tool_id: str
    tool_key: str
    tool_version: int


class ToolRunDetailResponse(typing.TypedDict):
    latest_cursor: int
    run: ToolRunSummaryResponse
    steps: list[ToolStepResponse]


class ToolRunListResponse(typing.TypedDict):
    items: list[ToolRunSummaryResponse]


class ToolRunSummaryResponse(typing.TypedDict):
    agent_release_id: str
    agent_release_version: int
    cancel_requested_at: str | None
    completed_at: str | None
    completed_step_count: int
    created_at: str
    deadline_at: str
    max_attempts_per_step: int
    max_cost_microunits: int
    max_execution_seconds: int
    max_steps: int
    pending_confirmation_count: int
    requested_by_account_id: str
    run_id: str
    service_id: str
    service_name: str
    state: str
    step_count: int
    total_cost_microunits: int
    updated_at: str
    version: int


class ToolStepResponse(typing.TypedDict):
    access_mode: str
    attempts: list[ToolAttemptResponse]
    canonical_arguments_hash: str
    confirmation: ToolConfirmationResponse | None
    created_at: str
    current_attempt_no: int | None
    display_name: str
    max_attempts: int
    max_result_bytes: int
    risk_level: str
    sequence_no: int
    state: str
    step_id: str
    timeout_seconds: int
    tool_id: str
    tool_key: str
    tool_version: int
    updated_at: str


class TransferApprovalRequest(typing.TypedDict):
    idempotency_key: str
    reason_code: typing.NotRequired[str | None]
    target_account_id: str


class UpdateAgentDraftRequest(typing.TypedDict):
    configuration: dict[str, object]
    expected_revision: int


class UpdateServiceRequest(typing.TypedDict):
    allowed_account_ids: typing.NotRequired[list[str]]
    allowed_department_ids: typing.NotRequired[list[str]]
    expected_version: int
    name: typing.NotRequired[str | None]
    target_status: typing.NotRequired[typing.Literal["active", "suspended", "archived"] | None]
    visibility: typing.NotRequired[typing.Literal["workspace", "restricted"] | None]


class UpdateWorkflowDraftRequest(typing.TypedDict):
    expected_revision: int
    graph: WorkflowGraphDocument


class UploadMetadataResponse(typing.TypedDict):
    content_hash: str
    media_type: str
    scan_status: typing.NotRequired[str]
    size_bytes: int


class UsageReconciliationListResponse(typing.TypedDict):
    items: list[UsageReconciliationResponse]


class UsageReconciliationResponse(typing.TypedDict):
    consistent: bool
    counter_value: int | None
    counter_version: int | None
    latest_resulting_value: int | None
    metric: typing.Literal[
        "storage_bytes", "knowledge_bases", "published_agents", "questions_monthly"
    ]
    period_key: str
    record_count: int
    record_delta_total: int


class UsageRecordPageResponse(typing.TypedDict):
    items: list[UsageRecordResponse]
    next_cursor: str | None


class UsageRecordResponse(typing.TypedDict):
    delta_value: int
    idempotency_key: str
    metric: typing.Literal[
        "storage_bytes", "knowledge_bases", "published_agents", "questions_monthly"
    ]
    occurred_at: str
    period_key: str
    resulting_value: int
    usage_record_id: str
    workspace_id: str


class UserMessageCreatedResponse(typing.TypedDict):
    message: MessageResponse
    run: AssistantRunResponse


class WorkflowDefinitionResponse(typing.TypedDict):
    created_at: str
    created_by_account_id: str
    current_version_id: str | None
    description: str | None
    name: str
    status: typing.Literal["active", "archived"]
    updated_at: str
    version: int
    workflow_id: str
    workspace_id: str


class WorkflowDetailResponse(typing.TypedDict):
    draft: WorkflowDraftResponse
    publication: WorkflowPublicationResponse | None
    workflow: WorkflowDefinitionResponse


class WorkflowDraftResponse(typing.TypedDict):
    graph: WorkflowGraphDocument
    graph_digest: str
    revision: int
    updated_at: str
    updated_by_account_id: str
    validation_errors: list[WorkflowGraphViolationResponse]
    workflow_id: str
    workspace_id: str


class WorkflowEdgeDocument(typing.TypedDict):
    condition_key: typing.NotRequired[str | None]
    edge_id: str
    source_node_id: str
    target_node_id: str


class WorkflowGraphDocument(typing.TypedDict):
    edges: list[WorkflowEdgeDocument]
    entry_node_id: str
    nodes: list[WorkflowNodeDocument]
    schema_version: typing.NotRequired[int]


class WorkflowGraphViolationResponse(typing.TypedDict):
    code: str
    edge_id: str | None
    node_id: str | None


class WorkflowListResponse(typing.TypedDict):
    items: list[WorkflowDefinitionResponse]


class WorkflowNodeDocument(typing.TypedDict):
    config: typing.NotRequired[dict[str, object]]
    name: str
    node_id: str
    node_type: typing.Literal[
        "trigger", "condition", "knowledge_retrieval", "model", "approval", "result"
    ]


class WorkflowPublicationResponse(typing.TypedDict):
    generation: int
    published_at: str
    published_by_account_id: str
    workflow_id: str
    workflow_version_id: str
    workspace_id: str


class WorkflowPublishResponse(typing.TypedDict):
    publication: WorkflowPublicationResponse
    version: WorkflowVersionResponse
    workflow: WorkflowDefinitionResponse


class WorkflowRunListResponse(typing.TypedDict):
    items: list[WorkflowRunResponse]


class WorkflowRunResponse(typing.TypedDict):
    completed_at: str | None
    created_at: str
    error_code: str | None
    executor_version: typing.NotRequired[str | None]
    input_payload: dict[str, object] | None
    model_calls: typing.NotRequired[int]
    output_bytes: typing.NotRequired[int]
    output_payload: typing.NotRequired[dict[str, object] | None]
    requested_by_account_id: str
    retrieval_calls: typing.NotRequired[int]
    status: typing.Literal[
        "queued", "running", "waiting_approval", "succeeded", "failed", "cancelled"
    ]
    steps_executed: typing.NotRequired[int]
    updated_at: str
    version: int
    workflow_id: str
    workflow_run_id: str
    workflow_version_id: str
    workspace_id: str


class WorkflowVersionResponse(typing.TypedDict):
    graph: WorkflowGraphDocument
    graph_digest: str
    published_at: str
    published_by_account_id: str
    source_draft_revision: int
    version_number: int
    workflow_id: str
    workflow_version_id: str
    workspace_id: str


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
