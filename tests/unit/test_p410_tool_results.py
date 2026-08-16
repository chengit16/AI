"""验证 P4-10 安全结果、完整用量和低基数工具观测边界。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest
from ai_platform_api.modules.tool_execution.application.errors import ToolResultRejectedError
from ai_platform_api.modules.tool_execution.application.results import ToolResultFactsService
from ai_platform_api.modules.tool_execution.domain.adapters import ToolAdapterResult
from ai_platform_backend.observability.runtime import ObservabilityRuntime

ROOT = Path(__file__).parents[2]
REGISTRY_PATH = ROOT / "contracts" / "observability" / "field-registry.v1.json"
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000410")
TOOL_CALL_ID = UUID("76000000-0000-4000-8000-000000000410")
RECORDED_AT = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
SHA256_A = "a" * 64
SHA256_B = "b" * 64
SENSITIVE_MARKER = "SYNTHETIC_SENSITIVE_MARKER"


def _adapter_result() -> ToolAdapterResult:
    return ToolAdapterResult(
        tool_key="knowledge.search",
        tool_version=1,
        payload={"items": [{"title": SENSITIVE_MARKER}]},
        output_schema_hash=SHA256_A,
        result_sha256=SHA256_B,
        result_size_bytes=128,
        checks=("schema", "size", "sensitive_fields", "prompt_injection"),
    )


def test_accepted_result_keeps_only_digest_checks_and_complete_usage() -> None:
    facts = ToolResultFactsService().accepted(
        workspace_id=WORKSPACE_ID,
        tool_call_id=TOOL_CALL_ID,
        adapter_result=_adapter_result(),
        duration_ms=37,
        cost_microunits=11,
        recorded_at=RECORDED_AT,
    )

    assert facts.outcome == "succeeded"
    assert facts.duration_ms == 37
    assert facts.cost_microunits == 11
    assert facts.result_size_bytes == 128
    assert facts.safe_result is not None
    assert facts.safe_result.content_hash == SHA256_B
    assert facts.safe_result.output_schema_hash == SHA256_A
    assert facts.safe_result.eligible_for_model_context is True
    assert {item.check_code: item.status for item in facts.safe_result.safety_checks} == {
        "schema": "passed",
        "size": "passed",
        "sensitive_fields": "passed",
        "prompt_injection": "passed",
    }
    assert SENSITIVE_MARKER not in repr(facts)


def test_rejected_result_is_ineligible_and_failure_usage_is_not_dropped() -> None:
    service = ToolResultFactsService()
    facts = service.rejected(
        workspace_id=WORKSPACE_ID,
        tool_call_id=TOOL_CALL_ID,
        output_schema_hash=SHA256_A,
        content_hash=SHA256_B,
        check_statuses={
            "schema": "passed",
            "size": "passed",
            "sensitive_fields": "passed",
            "prompt_injection": "failed",
        },
        result_size_bytes=2048,
        duration_ms=19,
        cost_microunits=7,
        recorded_at=RECORDED_AT,
    )
    failed = service.failed(
        outcome="timed_out",
        duration_ms=5000,
        cost_microunits=23,
        result_size_bytes=0,
        error_code="TOOL_TIMEOUT",
    )

    assert facts.outcome == "failed"
    assert facts.error_code == "TOOL_RESULT_REJECTED"
    assert facts.safe_result is not None
    assert facts.safe_result.status == "rejected"
    assert facts.safe_result.eligible_for_model_context is False
    assert failed.outcome == "timed_out"
    assert failed.cost_microunits == 23
    assert failed.safe_result is None


def test_result_validation_rejects_missing_checks_credentials_and_negative_usage() -> None:
    service = ToolResultFactsService()
    accepted = service.accepted(
        workspace_id=WORKSPACE_ID,
        tool_call_id=TOOL_CALL_ID,
        adapter_result=_adapter_result(),
        duration_ms=1,
        cost_microunits=0,
        recorded_at=RECORDED_AT,
    )
    assert accepted.safe_result is not None

    with pytest.raises(ToolResultRejectedError):
        service.accepted(
            workspace_id=WORKSPACE_ID,
            tool_call_id=TOOL_CALL_ID,
            adapter_result=replace(_adapter_result(), checks=("schema", "size")),
            duration_ms=1,
            cost_microunits=0,
            recorded_at=RECORDED_AT,
        )
    with pytest.raises(ToolResultRejectedError):
        service.validate(
            replace(
                accepted,
                safe_result=replace(accepted.safe_result, credential_exposure_detected=True),
            )
        )
    with pytest.raises(ToolResultRejectedError):
        service.failed(
            outcome="failed",
            duration_ms=1,
            cost_microunits=-1,
            result_size_bytes=0,
            error_code="TOOL_ADAPTER_UNAVAILABLE",
        )


def test_tool_metrics_keep_failures_and_cost_without_high_cardinality_labels() -> None:
    output = StringIO()
    runtime = ObservabilityRuntime(
        service_name="ai-platform-worker",
        environment="test",
        field_registry_path=REGISTRY_PATH,
        log_stream=output,
    )
    try:
        runtime.record_tool_execution(
            access_mode="read",
            risk_level="low",
            outcome="succeeded",
            duration_ms=12,
            cost_microunits=3,
            result_size_bytes=128,
        )
        runtime.record_tool_execution(
            access_mode="write",
            risk_level="high",
            outcome="failed",
            duration_ms=18,
            cost_microunits=9,
            result_size_bytes=256,
            failed_check="prompt_injection",
            error_code="TOOL_RESULT_REJECTED",
        )
        metrics = runtime.metrics_payload().decode("utf-8")
        logs = [json.loads(line) for line in output.getvalue().splitlines()]

        assert 'outcome="succeeded"' in metrics
        assert 'outcome="failed"' in metrics
        assert 'check_code="prompt_injection"' in metrics
        assert "ai_platform_tool_cost_microunits_total" in metrics
        assert "workspace_id" not in metrics
        assert "tool_call_id" not in metrics
        assert [item["cost_microunits"] for item in logs] == [3, 9]
        assert all("payload" not in item and "credential" not in item for item in logs)
    finally:
        runtime.shutdown()


def test_tool_metrics_reject_unbounded_labels_before_export() -> None:
    runtime = ObservabilityRuntime(
        service_name="ai-platform-worker",
        environment="test",
        field_registry_path=REGISTRY_PATH,
    )
    try:
        with pytest.raises(ValueError, match="未冻结分类"):
            runtime.record_tool_execution(
                access_mode=str(WORKSPACE_ID),
                risk_level="low",
                outcome="failed",
                duration_ms=1,
                cost_microunits=1,
                result_size_bytes=0,
            )
    finally:
        runtime.shutdown()
