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
        "RETRIEVAL_SCOPE_DENIED",
        "RETRIEVAL_CONFIGURATION_ERROR",
        "CITATION_INVALID",
        "MODEL_REQUEST_REJECTED",
        "MODEL_DATA_BOUNDARY_DENIED",
        "MODEL_ROUTE_UNAVAILABLE",
        "MODEL_GATEWAY_UNAVAILABLE",
        "SSE_EVENT_EXPIRED",
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
