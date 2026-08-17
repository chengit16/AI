"""验证 P5-12 控制台契约、状态保真、空间隔离和危险操作边界。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.operations.application.control_tower import (
    OperationsControlTowerDeniedError,
    OperationsControlTowerService,
)
from ai_platform_api.modules.operations.domain.control_tower import ControlTowerSnapshotSource
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[1]
BASELINE = ROOT / "contracts/operations/control-tower-baseline.v1.json"
SCHEMA = ROOT / "contracts/operations/control-tower-baseline.v1.schema.json"
WORKSPACE_ID = UUID("55000000-0000-4000-8000-000000000512")
OTHER_WORKSPACE_ID = UUID("55000000-0000-4000-8000-000000000513")


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def context(permission: str, workspace_id: UUID = WORKSPACE_ID) -> RequestContext:
    return replace(
        RequestContext.trusted(
            actor_id=UUID("55000000-0000-4000-8000-000000000514"),
            user_id=UUID("55000000-0000-4000-8000-000000000515"),
            workspace_id=workspace_id,
            trace=TraceContext.continue_from(
                "00-b123456789abcdef0123456789abcdef-b123456789abcdef-01"
            ),
            authentication_method="browser_session",
        ),
        authorized_permission_code=permission,
        authorized_policy_decision_id=uuid4(),
        authorized_policy_version=1,
        authorized_workspace=True,
    )


def test_p512_baseline_matches_schema_and_keeps_external_inputs_unconfigured() -> None:
    schema = load_object(SCHEMA)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(load_object(BASELINE))
    baseline = load_object(BASELINE)
    sections = cast(list[dict[str, Any]], baseline["sections"])
    assert {item["key"] for item in sections} == {
        "quality",
        "cost",
        "isolation",
        "compliance",
        "private_instance",
    }
    assert all(item["status"] == "blocked" for item in sections)
    assert all(item["confirmation_required"] for item in baseline["dangerous_operations"])
    assert all(item["backend_reauthorization"] for item in baseline["dangerous_operations"])
    api_dockerfile = (ROOT / "infra/docker/api.Dockerfile").read_text(encoding="utf-8")
    assert "COPY contracts/operations ./contracts/operations" in api_dockerfile


def test_p512_snapshot_is_workspace_scoped_and_statuses_are_not_reinterpreted() -> None:
    source = ControlTowerSnapshotSource(BASELINE)
    service = OperationsControlTowerService(source)
    snapshot = service.snapshot(
        context("operations.control_tower.read"),
        workspace_id=WORKSPACE_ID,
        now=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
    )
    assert snapshot.workspace_id == WORKSPACE_ID
    assert snapshot.generated_at.isoformat() == "2026-08-18T01:00:00+00:00"
    assert {section.status for section in snapshot.sections} == {"blocked"}
    statuses = {fact.status for section in snapshot.sections for fact in section.facts}
    assert "not_configured" in statuses
    assert "not_run" in statuses
    assert all(
        item.confirmation_required and item.backend_reauthorization
        for item in snapshot.dangerous_operations
    )
    with pytest.raises(OperationsControlTowerDeniedError):
        service.snapshot(context("operations.records.read"), workspace_id=WORKSPACE_ID)
    with pytest.raises(OperationsControlTowerDeniedError):
        service.snapshot(
            context("operations.control_tower.read", OTHER_WORKSPACE_ID),
            workspace_id=WORKSPACE_ID,
        )
