"""验证 API 健康探针在本地、容器依赖和降级场景中的响应。"""

from unittest.mock import patch

from ai_platform_api.config import Settings, get_settings
from ai_platform_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_liveness() -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "service": "ai-platform-api",
        "status": "ok",
        "version": "0.0.0",
        "environment": "local",
        "checks": {"api": "ok"},
    }


def test_readiness_includes_configuration_check() -> None:
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"] == {"api": "ok", "configuration": "ok"}


def test_readiness_returns_503_when_dependency_is_unavailable() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(dependency_checks_enabled=True)
    try:
        with patch(
            "ai_platform_api.modules.system.api.health.dependency_checks",
            return_value={"postgres": False},
        ):
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["postgres"] == "degraded"
