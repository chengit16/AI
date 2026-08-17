"""验证 P5-07 隔离等级、套餐资格、合规状态和迁移状态机契约。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from ai_platform_api.modules.isolation.application.policy import (
    ISOLATION_LEVELS,
    MIGRATION_TRANSITIONS,
    PLAN_MAXIMUM_LEVELS,
    evaluate_isolation_policy,
)
from ai_platform_api.modules.isolation.domain.models import (
    ACTIVE_MIGRATION_STATUSES,
    InvalidIsolationMigrationTransitionError,
    IsolationComplianceAssessment,
    WorkspaceIsolationEntitlement,
    WorkspaceIsolationMigrationPlan,
)
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "isolation"
SCHEMA_PATH = CONTRACT_DIR / "workspace-isolation-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "workspace-isolation-baseline.v1.json"
WORKSPACE_ID = UUID("57000000-0000-4000-8000-000000000571")
ACTOR_ID = UUID("57000000-0000-4000-8000-000000000572")
POLICY_ID = UUID("57000000-0000-4000-8000-000000000573")
PLAN_ID = UUID("57000000-0000-4000-8000-000000000574")
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _entitlement(
    *,
    workspace_type: str = "enterprise",
    plan_code: str = "enterprise_simulated",
) -> WorkspaceIsolationEntitlement:
    return WorkspaceIsolationEntitlement(
        WORKSPACE_ID,
        cast(Any, workspace_type),
        "active",
        plan_code,
        1,
    )


def test_p507_baseline_matches_versioned_schema_and_policy_constants() -> None:
    schema = _load(SCHEMA_PATH)
    baseline = _load(BASELINE_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(baseline)

    assert tuple(baseline["levels"]) == ISOLATION_LEVELS
    assert baseline["plan_maximum_levels"] == PLAN_MAXIMUM_LEVELS
    assert tuple(baseline["active_migration_statuses"]) == ACTIVE_MIGRATION_STATUSES
    assert {status: sorted(targets) for status, targets in MIGRATION_TRANSITIONS.items()} == {
        status: sorted(targets) for status, targets in baseline["migration_transitions"].items()
    }


def test_p507_personal_and_unknown_plan_cannot_raise_isolation_level() -> None:
    personal = evaluate_isolation_policy(
        _entitlement(workspace_type="personal", plan_code="personal_local"),
        "L2",
        IsolationComplianceAssessment("not_required", None),
    )
    unknown = evaluate_isolation_policy(
        _entitlement(plan_code="unknown_plan"),
        "L2",
        IsolationComplianceAssessment("not_required", None),
    )

    assert personal.decision == "denied"
    assert "personal_workspace_l1_only" in personal.reason_codes
    assert unknown.decision == "denied"
    assert "plan_not_supported" in unknown.reason_codes


def test_p507_l2_is_plan_eligible_but_l3_requires_compliance_configuration() -> None:
    l2 = evaluate_isolation_policy(
        _entitlement(),
        "L2",
        IsolationComplianceAssessment("not_required", None),
    )
    l3 = evaluate_isolation_policy(
        _entitlement(plan_code="enterprise_isolated"),
        "L3",
        IsolationComplianceAssessment("not_configured", None),
    )
    approved_l3 = evaluate_isolation_policy(
        _entitlement(plan_code="enterprise_isolated"),
        "L3",
        IsolationComplianceAssessment("approved", "a" * 64),
    )

    assert l2.decision == "allowed"
    assert l2.reason_codes == ()
    assert l3.decision == "not_configured"
    assert l3.reason_codes == ("compliance_policy_not_configured",)
    assert approved_l3.decision == "allowed"


def test_p507_migration_state_machine_rejects_skips_and_terminal_rewrites() -> None:
    plan = WorkspaceIsolationMigrationPlan(
        PLAN_ID,
        WORKSPACE_ID,
        POLICY_ID,
        "L1",
        "L2",
        "planned",
        "b" * 64,
        ACTOR_ID,
        NOW,
        ACTOR_ID,
        NOW,
        1,
    )
    approved = plan.transition(
        "approved",
        actor_id=ACTOR_ID,
        occurred_at=NOW,
        allowed=MIGRATION_TRANSITIONS,
    )
    with pytest.raises(InvalidIsolationMigrationTransitionError):
        plan.transition(
            "switch_ready",
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            allowed=MIGRATION_TRANSITIONS,
        )
    with pytest.raises(InvalidIsolationMigrationTransitionError):
        approved.transition(
            "completed",
            actor_id=ACTOR_ID,
            occurred_at=NOW,
            allowed=MIGRATION_TRANSITIONS,
        )
