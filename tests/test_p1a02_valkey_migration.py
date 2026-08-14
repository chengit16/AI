"""验证 P1A-02 Redis 到 Valkey 的配置和编排迁移。"""

from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import yaml  # type: ignore[import-untyped]
from ai_platform_api.config import Settings
from ai_platform_api.modules.system.api.health import dependency_checks

ROOT = Path(__file__).parents[1]


def load_compose() -> dict[str, Any]:
    # Compose 文件是仓库受控输入；读取后仍验证顶层类型，再进入结构断言。
    document = yaml.safe_load((ROOT / "infra/compose/compose.yaml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("Compose 顶层必须是对象")
    return cast(dict[str, Any], document)


def test_settings_use_valkey_resp_url() -> None:
    settings = Settings()

    assert settings.valkey_url == "redis://127.0.0.1:6379/0"
    assert not hasattr(settings, "redis_url")


def test_dependency_health_reports_valkey() -> None:
    settings = Settings(dependency_checks_enabled=True)

    with (
        patch("ai_platform_api.modules.system.api.health.check_tcp", return_value=True),
        patch("ai_platform_api.modules.system.api.health.check_http", return_value=True),
    ):
        checks = dependency_checks(settings)

    assert checks == {
        "postgres": True,
        "valkey": True,
        "object_storage": True,
        "document_parser": True,
    }


def test_compose_pins_valkey_and_removes_redis_service() -> None:
    compose = load_compose()
    services = compose["services"]
    valkey = services["valkey"]

    assert "redis" not in services
    assert valkey["image"] == (
        "valkey/valkey:8.1.5-alpine@"
        "sha256:918228e4ff7da6b3a4213cb18067f6e09d9f0503d0a08868699ba227cff71861"
    )
    assert valkey["command"] == ["valkey-server", "--appendonly", "yes"]
    assert valkey["healthcheck"]["test"] == ["CMD", "valkey-cli", "ping"]
    assert any("/data/valkey:/data" in volume for volume in valkey["volumes"])
    assert services["api"]["depends_on"]["valkey"]["condition"] == "service_healthy"
    assert services["worker"]["depends_on"]["valkey"]["condition"] == "service_healthy"
