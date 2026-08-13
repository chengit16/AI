from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from ai_platform_api.app.dependencies import ApplicationContainer
from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.app.factory import create_app
from ai_platform_api.config import Settings
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.infrastructure.security import EnvelopeSecretCipher
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.modules.release.application.startup import verify_release_compatibility
from ai_platform_api.modules.release.domain.errors import (
    ReleaseCombinationIncompatibleError,
    ReleaseManifestInvalidError,
)
from ai_platform_api.modules.retrieval.domain.errors import CitationInvalidError
from ai_platform_api.persistence.database import PlatformDatabase
from fastapi import APIRouter
from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[3]


class ClosingDatabase:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ClosingSessions:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def application_container(settings: Settings) -> tuple[ApplicationContainer, ClosingDatabase]:
    database = ClosingDatabase()
    sessions = ClosingSessions()
    container = ApplicationContainer(
        settings=settings,
        database=cast(PlatformDatabase, database),
        errors=ErrorCatalog.load(ROOT / "contracts/errors/catalog.v1.json"),
        authentication=cast("AuthenticationService", object()),
        api_keys=cast("ApiKeyService", object()),
        secret_cipher=cast("EnvelopeSecretCipher", object()),
        sessions=cast("ValkeySessionStore", sessions),
    )
    return container, database


def test_factory_uses_injected_settings_and_closes_dependencies() -> None:
    settings = Settings(app_name="synthetic-api", environment="test")
    container, database = application_container(settings)
    application = create_app(settings, container)

    with TestClient(application) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json()["service"] == "synthetic-api"
    assert response.json()["environment"] == "test"
    assert database.closed is True


def test_factory_rejects_container_from_another_configuration() -> None:
    settings = Settings(environment="synthetic-a", session_cookie_secure=True)
    container, _ = application_container(
        Settings(environment="synthetic-b", session_cookie_secure=True)
    )

    try:
        create_app(settings, container)
    except ValueError as error:
        assert str(error) == "应用配置与依赖容器配置不一致"
    else:
        raise AssertionError("配置不一致时必须拒绝应用装配")


@contextmanager
def error_client() -> Iterator[TestClient]:
    settings = Settings(environment="test")
    container, _ = application_container(settings)
    application = create_app(settings, container)
    router = APIRouter(prefix="/synthetic")

    @router.get("/known")
    def known_error() -> None:
        raise CitationInvalidError

    @router.get("/unknown")
    def unknown_error() -> None:
        raise RuntimeError("synthetic-secret-value")

    @router.get("/validated")
    def validated(limit: int) -> dict[str, int]:
        return {"limit": limit}

    application.include_router(router)
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client


def test_known_platform_error_uses_catalog_and_trusted_trace() -> None:
    parent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    request_id = "10000000-0000-4000-8000-000000000001"

    with error_client() as client:
        response = client.get(
            "/synthetic/known",
            headers={"traceparent": parent, "x-request-id": request_id},
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "CITATION_INVALID",
        "message": "引用不存在、不可见、版本失效或与原文不一致",
        "retryable": False,
        "request_id": request_id,
        "trace_id": "0123456789abcdef0123456789abcdef",
    }


def test_unknown_error_does_not_expose_internal_message() -> None:
    with error_client() as client:
        response = client.get("/synthetic/unknown")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "synthetic-secret-value" not in response.text


def test_framework_404_uses_stable_non_disclosing_error() -> None:
    with error_client() as client:
        response = client.get("/synthetic/missing")

    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert "Not Found" not in response.text


def test_request_validation_uses_stable_error() -> None:
    with error_client() as client:
        response = client.get("/synthetic/validated", params={"limit": "invalid"})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "int_parsing" not in response.text


def test_method_not_allowed_uses_stable_error() -> None:
    with error_client() as client:
        response = client.post("/synthetic/known")

    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
    assert "Method Not Allowed" not in response.text


def test_startup_accepts_frozen_release_combination() -> None:
    verify_release_compatibility(
        str(ROOT / "contracts/fixtures/release-manifest.v1.valid.json"),
        str(ROOT / "contracts/release/compatibility-matrix.v1.json"),
    )


def test_startup_rejects_missing_release_manifest(tmp_path: Path) -> None:
    try:
        verify_release_compatibility(
            str(tmp_path / "missing.json"),
            str(ROOT / "contracts/release/compatibility-matrix.v1.json"),
        )
    except ReleaseManifestInvalidError:
        pass
    else:
        raise AssertionError("发布清单缺失时必须拒绝启动")


def test_startup_rejects_tampered_manifest_schema_version(tmp_path: Path) -> None:
    manifest = (ROOT / "contracts/fixtures/release-manifest.v1.valid.json").read_text(
        encoding="utf-8"
    )
    tampered = tmp_path / "tampered-schema.json"
    tampered.write_text(
        manifest.replace('"schema_version": 1', '"schema_version": 2'),
        encoding="utf-8",
    )

    try:
        verify_release_compatibility(
            str(tampered),
            str(ROOT / "contracts/release/compatibility-matrix.v1.json"),
        )
    except ReleaseManifestInvalidError:
        pass
    else:
        raise AssertionError("发布清单版本被篡改时必须拒绝启动")


def test_startup_rejects_incompatible_release_combination(tmp_path: Path) -> None:
    manifest = (ROOT / "contracts/fixtures/release-manifest.v1.valid.json").read_text(
        encoding="utf-8"
    )
    incompatible = tmp_path / "incompatible.json"
    incompatible.write_text(
        manifest.replace('"schema_revision": "20260813_0005"', '"schema_revision": "unknown"'),
        encoding="utf-8",
    )

    try:
        verify_release_compatibility(
            str(incompatible),
            str(ROOT / "contracts/release/compatibility-matrix.v1.json"),
        )
    except ReleaseCombinationIncompatibleError as error:
        assert error.reasons == (
            "MANIFEST_DIGEST_MISMATCH",
            "DATABASE_REVISION_INCOMPATIBLE",
        )
    else:
        raise AssertionError("发布组合不兼容时必须拒绝启动")
