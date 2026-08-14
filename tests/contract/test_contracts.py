import json
from pathlib import Path
from typing import Any, cast

import pytest
from ai_platform_api.main import app
from ai_platform_api.modules.release.application.manifest import ReleaseManifestService
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from scripts.generate_release_manifest import parse_inputs

ROOT = Path(__file__).parents[2]
CONTRACTS = ROOT / "contracts"


def load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"契约文件顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def contract_registry() -> Registry[Any]:
    registry: Registry[Any] = Registry()
    for schema_path in CONTRACTS.rglob("*.schema.json"):
        schema = load_json(schema_path)
        schema_id = schema.get("$id")
        if isinstance(schema_id, str):
            registry = registry.with_resource(schema_id, Resource.from_contents(schema))
    return registry


CONTRACT_REGISTRY = contract_registry()


def assert_valid(schema_path: str, fixture_path: str) -> None:
    schema = load_json(CONTRACTS / schema_path)
    fixture = load_json(CONTRACTS / fixture_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
        registry=CONTRACT_REGISTRY,
    ).validate(fixture)


def validator(schema_path: str) -> Draft202012Validator:
    schema = load_json(CONTRACTS / schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
        registry=CONTRACT_REGISTRY,
    )


def test_core_domain_contract_fixture() -> None:
    assert_valid(
        "domain/core-contracts.v1.schema.json",
        "fixtures/core-contracts.v1.valid.json",
    )


def test_policy_contract_fixture() -> None:
    assert_valid(
        "policy/decision.v1.schema.json",
        "fixtures/policy-decision.v1.valid.json",
    )


def test_authorization_resource_registry_contract() -> None:
    assert_valid(
        "authorization/resource-registry.v1.schema.json",
        "authorization/resource-registry.v1.json",
    )


def test_authorization_field_policy_registry_contract() -> None:
    assert_valid(
        "authorization/field-policy-registry.v1.schema.json",
        "authorization/field-policy-registry.v1.json",
    )


def test_sse_contract_fixture() -> None:
    assert_valid(
        "sse/message-event.v1.schema.json",
        "fixtures/message-event.v1.valid.json",
    )


def test_integration_event_contract_fixture() -> None:
    assert_valid(
        "events/integration-event.v1.schema.json",
        "fixtures/integration-event.v1.valid.json",
    )


def test_internal_task_envelope_contract_fixture() -> None:
    assert_valid(
        "events/internal-task-envelope.v1.schema.json",
        "fixtures/internal-task-envelope.v1.valid.json",
    )


def test_release_compatibility_matrix_contract() -> None:
    assert_valid(
        "release/compatibility-matrix.v1.schema.json",
        "release/compatibility-matrix.v1.json",
    )


def test_generated_release_manifest_contract() -> None:
    build_inputs = load_json(CONTRACTS / "fixtures/release-manifest-input.v1.valid.json")
    manifest = ReleaseManifestService().build(parse_inputs(build_inputs))

    validator("release/release-manifest.v1.schema.json").validate(manifest.to_dict())


def test_frozen_release_manifest_contract() -> None:
    assert_valid(
        "release/release-manifest.v1.schema.json",
        "fixtures/release-manifest.v1.valid.json",
    )


def test_integration_event_rejects_invalid_traceparent() -> None:
    fixture = load_json(CONTRACTS / "fixtures/integration-event.v1.valid.json")
    fixture["traceparent"] = "invalid"

    with pytest.raises(ValidationError):
        validator("events/integration-event.v1.schema.json").validate(fixture)


def test_error_codes_are_unique_and_stable() -> None:
    catalog = load_json(CONTRACTS / "errors/catalog.v1.json")
    validator("errors/catalog.v1.schema.json").validate(catalog)
    codes = [entry["code"] for entry in catalog["errors"]]

    assert catalog["schema_version"] == 1
    assert len(codes) == len(set(codes))
    assert {
        "POLICY_DENIED",
        "RELEASE_MANIFEST_INVALID",
        "RELEASE_COMBINATION_INCOMPATIBLE",
        "INGESTION_PARSE_FAILED",
        "INGESTION_PARSER_UNAVAILABLE",
        "INGESTION_SOURCE_CHANGED",
        "INGESTION_SOURCE_UNAVAILABLE",
        "INGESTION_ARTIFACT_UNAVAILABLE",
        "INGESTION_WORKER_LEASE_EXPIRED",
        "INDEX_ARTIFACT_UNAVAILABLE",
        "INDEX_ARTIFACT_CHANGED",
        "INDEX_ARTIFACT_INVALID",
        "INDEX_CHUNK_EMPTY",
        "INDEX_EMBEDDING_UNAVAILABLE",
        "INDEX_EMBEDDING_INVALID",
        "INDEX_WORKER_LEASE_EXPIRED",
        "INDEX_DOCUMENT_REVOKED",
        "RETRIEVAL_SCOPE_DENIED",
        "RETRIEVAL_CONFIGURATION_ERROR",
        "CITATION_INVALID",
        "MODEL_REQUEST_REJECTED",
        "MODEL_DATA_BOUNDARY_DENIED",
        "MODEL_ROUTE_UNAVAILABLE",
        "MODEL_GATEWAY_UNAVAILABLE",
        "SSE_EVENT_EXPIRED",
        "ORGANIZATION_CONFLICT",
        "ROLE_CONFLICT",
        "ENTITLEMENT_DENIED",
        "QUOTA_EXCEEDED",
        "ENTITLEMENT_CONFLICT",
        "INTERNAL_ERROR",
    }.issubset(codes)
    assert all(code == code.upper() for code in codes)


def test_core_contract_rejects_missing_workspace() -> None:
    fixture = load_json(CONTRACTS / "fixtures/core-contracts.v1.valid.json")
    del fixture["identity_context"]["workspace_id"]

    with pytest.raises(ValidationError):
        validator("domain/core-contracts.v1.schema.json").validate(fixture)


def test_policy_contract_rejects_unknown_decision() -> None:
    fixture = load_json(CONTRACTS / "fixtures/policy-decision.v1.valid.json")
    fixture["response"]["decision"] = "bypass"

    with pytest.raises(ValidationError):
        validator("policy/decision.v1.schema.json").validate(fixture)


def test_sse_contract_rejects_non_positive_sequence() -> None:
    fixture = load_json(CONTRACTS / "fixtures/message-event.v1.valid.json")
    fixture["sequence_no"] = 0

    with pytest.raises(ValidationError):
        validator("sse/message-event.v1.schema.json").validate(fixture)


def test_openapi_baseline_matches_fastapi_implementation() -> None:
    baseline = load_json(CONTRACTS / "openapi/platform-api.v1.json")
    generated = app.openapi()

    assert baseline["openapi"] == "3.1.0"
    assert generated == baseline


def test_identity_openapi_uses_stable_error_and_secret_schemas() -> None:
    baseline = load_json(CONTRACTS / "openapi/platform-api.v1.json")
    paths = baseline["paths"]
    schemas = baseline["components"]["schemas"]

    assert paths["/api/v1/auth/login"]["post"]["operationId"] == "loginWithPassword"
    assert paths["/api/v1/auth/register"]["post"]["operationId"] == "registerPersonalAccount"
    assert schemas["LoginRequest"]["properties"]["password"]["writeOnly"] is True
    assert schemas["RegistrationRequest"]["properties"]["password"]["writeOnly"] is True
    assert "HTTPValidationError" not in schemas
    for path, method in (
        ("/api/v1/auth/login", "post"),
        ("/api/v1/auth/register", "post"),
        ("/api/v1/auth/logout", "post"),
        ("/api/v1/auth/context", "get"),
    ):
        responses = paths[path][method]["responses"]
        assert responses["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_workspace_openapi_covers_enterprise_member_lifecycle() -> None:
    baseline = load_json(CONTRACTS / "openapi/platform-api.v1.json")
    paths = baseline["paths"]
    expected_operations = {
        "/api/v1/workspaces": ("get", "listAccessibleWorkspaces"),
        "/api/v1/workspaces/enterprise": ("post", "createEnterpriseWorkspace"),
        "/api/v1/workspaces/{workspace_id}/invitations": (
            "post",
            "inviteEnterpriseWorkspaceMember",
        ),
        "/api/v1/workspaces/invitations/{invitation_id}/accept": (
            "post",
            "acceptEnterpriseWorkspaceInvitation",
        ),
        "/api/v1/workspaces/{workspace_id}/switch": ("post", "switchWorkspaceContext"),
        "/api/v1/workspaces/{workspace_id}/leave": ("post", "leaveEnterpriseWorkspace"),
        "/api/v1/workspaces/{workspace_id}/members/{account_id}/disable": (
            "post",
            "disableEnterpriseWorkspaceMember",
        ),
        "/api/v1/workspaces/{workspace_id}/members": (
            "get",
            "listEnterpriseWorkspaceMembers",
        ),
    }
    for path, (method, operation_id) in expected_operations.items():
        assert paths[path][method]["operationId"] == operation_id
        assert paths[path][method]["responses"]["500"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }

    schemas = baseline["components"]["schemas"]
    assert schemas["WorkspaceSummaryResponse"]["properties"]["workspace_type"]["enum"] == [
        "personal",
        "enterprise",
    ]
    assert schemas["WorkspaceMembershipResponse"]["properties"]["membership_type"]["enum"] == [
        "owner",
        "member",
    ]


def test_organization_openapi_covers_tree_position_and_assignment_lifecycle() -> None:
    baseline = load_json(CONTRACTS / "openapi/platform-api.v1.json")
    paths = baseline["paths"]
    prefix = "/api/v1/workspaces/{workspace_id}/organization"
    expected_operations = {
        f"{prefix}/departments": {
            "post": "createEnterpriseDepartment",
            "get": "listEnterpriseDepartments",
        },
        f"{prefix}/departments/{{department_id}}/move": {"post": "moveEnterpriseDepartment"},
        f"{prefix}/departments/{{department_id}}/status": {"post": "setEnterpriseDepartmentStatus"},
        f"{prefix}/positions": {
            "post": "createEnterprisePosition",
            "get": "listEnterprisePositions",
        },
        f"{prefix}/positions/{{position_id}}/status": {"post": "setEnterprisePositionStatus"},
        f"{prefix}/members/{{account_id}}": {
            "put": "assignEnterpriseMemberOrganization",
            "get": "getEnterpriseMemberOrganization",
        },
    }
    for path, operations in expected_operations.items():
        for method, operation_id in operations.items():
            assert paths[path][method]["operationId"] == operation_id
            assert paths[path][method]["responses"]["500"]["content"]["application/json"][
                "schema"
            ] == {"$ref": "#/components/schemas/ErrorResponse"}

    schemas = baseline["components"]["schemas"]
    assert schemas["DepartmentResponse"]["required"] == [
        "department_id",
        "parent_department_id",
        "name",
        "status",
        "effective_active",
        "depth",
        "version",
    ]
    assert schemas["MemberOrganizationResponse"]["required"] == [
        "account_id",
        "department_ids",
        "primary_department_id",
        "position_ids",
        "membership_version",
    ]


def test_role_openapi_covers_role_binding_and_effective_resolution() -> None:
    baseline = load_json(CONTRACTS / "openapi/platform-api.v1.json")
    paths = baseline["paths"]
    prefix = "/api/v1/workspaces/{workspace_id}/roles"
    expected_operations = {
        prefix: {
            "post": "createEnterpriseRole",
            "get": "listEnterpriseRoles",
        },
        f"{prefix}/{{role_id}}/status": {"post": "setEnterpriseRoleStatus"},
        f"{prefix}/bindings": {"post": "bindEnterpriseRole"},
        f"{prefix}/bindings/{{binding_id}}/revoke": {"post": "revokeEnterpriseRoleBinding"},
        f"{prefix}/effective/{{account_id}}": {"get": "getEffectiveEnterpriseRoles"},
    }
    for path, operations in expected_operations.items():
        for method, operation_id in operations.items():
            assert paths[path][method]["operationId"] == operation_id
            assert paths[path][method]["responses"]["500"]["content"]["application/json"][
                "schema"
            ] == {"$ref": "#/components/schemas/ErrorResponse"}

    schemas = baseline["components"]["schemas"]
    assert schemas["CreateRoleBindingRequest"]["required"] == ["role_id", "scope_type"]
    assert schemas["EffectiveRoleSetResponse"]["required"] == [
        "account_id",
        "membership_id",
        "role_version",
        "roles",
    ]


def test_entitlement_openapi_covers_quota_and_feature_governance() -> None:
    baseline = load_json(CONTRACTS / "openapi/platform-api.v1.json")
    paths = baseline["paths"]
    prefix = "/api/v1/workspaces/{workspace_id}/entitlements"

    assert paths[prefix]["get"]["operationId"] == "getWorkspaceEntitlement"
    assert paths[f"{prefix}/features/open-api"]["post"]["operationId"] == (
        "setWorkspaceOpenApiFeature"
    )
    for path, method in ((prefix, "get"), (f"{prefix}/features/open-api", "post")):
        assert paths[path][method]["responses"]["500"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }

    schemas = baseline["components"]["schemas"]
    assert schemas["EntitlementResponse"]["required"] == [
        "workspace_id",
        "workspace_status",
        "plan_code",
        "entitlement_version",
        "open_api_allowed",
        "open_api_enabled",
        "public_publish_allowed",
        "quotas",
    ]
    assert schemas["QuotaResponse"]["properties"]["metric"]["enum"] == [
        "members",
        "storage_bytes",
        "knowledge_bases",
        "published_agents",
        "questions_monthly",
    ]


def test_knowledge_openapi_covers_fact_and_publication_lifecycle() -> None:
    baseline = load_json(CONTRACTS / "openapi/platform-api.v1.json")
    paths = baseline["paths"]
    prefix = "/api/v1/workspaces/{workspace_id}/knowledge-bases"
    expected_operations = {
        prefix: {"post": "createKnowledgeBase"},
        f"{prefix}/{{knowledge_base_id}}": {"delete": "deleteKnowledgeBase"},
        f"{prefix}/{{knowledge_base_id}}/documents": {"post": "createKnowledgeDocument"},
        f"{prefix}/{{knowledge_base_id}}/documents/upload": {"post": "uploadKnowledgeDocument"},
        f"{prefix}/{{knowledge_base_id}}/documents/{{document_id}}": {
            "delete": "deleteKnowledgeDocument"
        },
        f"{prefix}/{{knowledge_base_id}}/documents/{{document_id}}/versions": {
            "post": "createKnowledgeDocumentVersion"
        },
        f"{prefix}/{{knowledge_base_id}}/documents/{{document_id}}/versions/upload": {
            "post": "uploadKnowledgeDocumentVersion"
        },
        f"{prefix}/{{knowledge_base_id}}/documents/{{document_id}}/versions/"
        "{document_version_id}/ready": {"post": "markKnowledgeDocumentVersionReady"},
        f"{prefix}/{{knowledge_base_id}}/documents/{{document_id}}/versions/"
        "{document_version_id}/publish": {"post": "publishKnowledgeDocumentVersion"},
    }
    for path, operations in expected_operations.items():
        for method, operation_id in operations.items():
            assert paths[path][method]["operationId"] == operation_id
            assert paths[path][method]["responses"]["500"]["content"]["application/json"][
                "schema"
            ] == {"$ref": "#/components/schemas/ErrorResponse"}

    schemas = baseline["components"]["schemas"]
    assert "original_object_key" not in schemas["DocumentSourceResponse"]["properties"]
    assert (
        schemas["CreateDocumentRequest"]["properties"]["original_object_key"]["deprecated"] is True
    )
    assert (
        schemas["CreateDocumentVersionRequest"]["properties"]["original_object_key"]["deprecated"]
        is True
    )
    assert "source_url" not in schemas["DocumentSourceResponse"]["properties"]
    assert schemas["DocumentVersionResponse"]["properties"]["status"]["enum"] == [
        "draft",
        "ready",
        "published",
        "superseded",
    ]
