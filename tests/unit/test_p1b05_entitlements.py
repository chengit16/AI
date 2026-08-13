from datetime import UTC, datetime
from uuid import UUID

from ai_platform_api.modules.identity.domain.entitlements import default_entitlement

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000041")
NOW = datetime(2026, 8, 14, tzinfo=UTC)


def test_default_entitlements_keep_personal_and_enterprise_on_same_model() -> None:
    personal, personal_settings = default_entitlement(
        workspace_id=WORKSPACE_ID,
        workspace_type="personal",
        occurred_at=NOW,
    )
    enterprise, enterprise_settings = default_entitlement(
        workspace_id=WORKSPACE_ID,
        workspace_type="enterprise",
        occurred_at=NOW,
    )

    assert personal.plan_code == "personal_local"
    assert personal.max_storage_bytes == 5 * 1024**3
    assert personal.max_members == 1
    assert personal.max_knowledge_bases == 5
    assert personal.max_published_agents == 3
    assert personal.max_monthly_questions == 2_000
    assert personal.open_api_allowed is False
    assert enterprise.plan_code == "enterprise_simulated"
    assert enterprise.max_storage_bytes == 100 * 1024**3
    assert enterprise.max_members == 100
    assert enterprise.max_knowledge_bases == 50
    assert enterprise.max_published_agents == 20
    assert enterprise.max_monthly_questions == 20_000
    assert enterprise.open_api_allowed is True
    assert personal_settings.open_api_enabled is False
    assert enterprise_settings.open_api_enabled is False
