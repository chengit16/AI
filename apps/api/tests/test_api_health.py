"""验证 API 健康探针在本地、容器依赖和降级场景中的响应。"""

from datetime import UTC, datetime
from unittest.mock import patch

from ai_platform_api.config import Settings, get_settings
from ai_platform_api.main import app
from ai_platform_api.modules.system.api.health import DependencyHealth
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
            "ai_platform_api.modules.system.api.health.dependency_health_checks",
            return_value={
                "postgres": DependencyHealth(
                    status="degraded",
                    critical=True,
                    latency_ms=1.5,
                    checked_at=datetime.now(UTC),
                    reason_code="dependency_unavailable",
                )
            },
        ):
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["postgres"] == "degraded"
    assert response.json()["details"]["postgres"]["latency_ms"] == 1.5
    assert response.json()["details"]["postgres"]["reason_code"] == "dependency_unavailable"


def test_metrics_endpoint_exposes_only_registered_runtime_metrics() -> None:
    response = client.get("/api/v1/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "ai_platform_http_requests_total" in response.text
    assert "ai_platform_dependency_health" in response.text
    assert "ai_platform_outbox_oldest_pending_age_seconds" in response.text
    assert "workspace_id" not in response.text
    assert "SYNTHETIC_SENSITIVE_MARKER" not in response.text
