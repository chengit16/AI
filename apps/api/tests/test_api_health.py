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
