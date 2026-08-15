"""验证 P2-06 字段白名单、完整 Trace、指标和告警安全基线。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from io import StringIO
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
import yaml  # type: ignore[import-untyped]
from ai_platform_api.config import Settings
from ai_platform_backend.integration.trace import TraceContext
from ai_platform_backend.observability.fields import UnsafeObservabilityFieldError
from ai_platform_backend.observability.runtime import ObservabilityRuntime, SafeJsonFormatter
from ai_platform_worker.config import WorkerSettings
from jsonschema import Draft202012Validator
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "contracts" / "observability" / "field-registry.v1.json"
SCHEMA_PATH = ROOT / "contracts" / "observability" / "field-registry.v1.schema.json"
RULES_PATH = ROOT / "infra" / "observability" / "prometheus-rules.yml"
SENSITIVE_MARKER = "SYNTHETIC_SENSITIVE_MARKER"
CRITICAL_COMPONENTS = {"http", "task", "retrieval", "model", "workflow", "approval", "outbox"}


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _runtime(
    exporter: InMemorySpanExporter | None = None,
    log_stream: StringIO | None = None,
) -> ObservabilityRuntime:
    return ObservabilityRuntime(
        service_name="ai-platform-synthetic",
        environment="test",
        field_registry_path=REGISTRY_PATH,
        span_exporter=exporter,
        log_stream=log_stream,
    )


def test_observability_registry_matches_schema_and_forbids_high_cardinality_labels() -> None:
    schema = _load_json(SCHEMA_PATH)
    registry = _load_json(REGISTRY_PATH)

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(registry)
    metric_fields = set(registry["channels"]["metric"]["allowed_fields"])
    assert {"trace_id", "request_id", "task_id", "workspace_id", "user_id"}.isdisjoint(
        metric_fields
    )
    assert {"prompt", "content", "payload", "authorization"}.issubset(
        set(registry["forbidden_field_fragments"])
    )


def test_otlp_endpoint_is_optional_locally_and_requires_https_outside_local() -> None:
    assert Settings(observability_otlp_endpoint="").observability_otlp_endpoint is None
    assert WorkerSettings(observability_otlp_endpoint="").observability_otlp_endpoint is None

    with pytest.raises(ValueError, match="HTTPS"):
        Settings(
            environment="production",
            session_cookie_secure=True,
            observability_otlp_endpoint="http://collector:4318",
        )
    with pytest.raises(ValueError, match="HTTPS"):
        WorkerSettings(
            environment="production", observability_otlp_endpoint="http://collector:4318"
        )


def test_unregistered_sensitive_data_is_rejected_before_all_exports() -> None:
    runtime = _runtime()
    try:
        with pytest.raises(UnsafeObservabilityFieldError):
            runtime.log("synthetic_sensitive", error_code=SENSITIVE_MARKER)
        with (
            pytest.raises(UnsafeObservabilityFieldError),
            runtime.start_span(
                "synthetic.span",
                component="test",
                operation="sensitive",
                attributes={"error_code": SENSITIVE_MARKER},
            ),
        ):
            pass
        with pytest.raises(UnsafeObservabilityFieldError):
            runtime.fields.validate("metric", {"workspace_id": SENSITIVE_MARKER})
        with pytest.raises(UnsafeObservabilityFieldError):
            runtime.alert(
                alert_name="synthetic_sensitive",
                component="test",
                severity="warning",
                status="firing",
                reason_code=SENSITIVE_MARKER,
                threshold=1,
                observed_value=1,
            )
    finally:
        runtime.shutdown()


def test_structured_log_contains_required_context_without_free_text() -> None:
    output = StringIO()
    runtime = _runtime(log_stream=output)
    try:
        with runtime.start_span(
            "synthetic.log",
            component="test",
            operation="log",
            parent=TraceContext.new(),
        ):
            runtime.log(
                "synthetic_log_completed",
                component="test",
                operation="log",
                outcome="success",
            )
        record = json.loads(output.getvalue().strip().splitlines()[-1])

        assert record["event_name"] == "synthetic_log_completed"
        assert record["service"] == "ai-platform-synthetic"
        assert record["environment"] == "test"
        assert len(record["trace_id"]) == 32
        assert len(record["span_id"]) == 16
        assert "message" not in record
    finally:
        runtime.shutdown()


def test_library_formatter_discards_original_message_and_exception_text() -> None:
    formatter = SafeJsonFormatter(
        service_name="ai-platform-synthetic",
        environment="test",
    )
    record = logging.LogRecord(
        name="synthetic.library",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=f"unsafe message {SENSITIVE_MARKER}",
        args=(),
        exc_info=None,
    )

    payload = formatter.format(record)
    document = json.loads(payload)
    assert SENSITIVE_MARKER not in payload
    assert document == {
        "environment": "test",
        "event_name": "unstructured_library_log",
        "level": "error",
        "service": "ai-platform-synthetic",
        "timestamp": document["timestamp"],
    }


def test_twenty_critical_samples_share_complete_trace_and_reach_coverage_target() -> None:
    exporter = InMemorySpanExporter()
    runtime = _runtime(exporter)
    request_id = UUID("10000000-0000-4000-8000-000000000206")
    try:
        for _ in range(20):
            with runtime.start_span(
                "http.server.request",
                component="http",
                operation="request",
                parent=TraceContext.new(),
            ):
                token = runtime.bind(request_id=request_id)
                try:
                    with runtime.task(task_name="synthetic.task.v1", queue="platform.control"):
                        for component, operation in (
                            ("retrieval", "plan"),
                            ("model", "invoke"),
                            ("workflow", "execute"),
                            ("approval", "act"),
                            ("outbox", "dispatch"),
                        ):
                            with runtime.operation(component=component, operation=operation):
                                pass
                finally:
                    runtime.reset(token)

        components_by_trace: dict[int, set[str]] = defaultdict(set)
        for span in exporter.get_finished_spans():
            attributes = span.attributes or {}
            span_component = attributes.get("platform.component")
            if isinstance(span_component, str):
                components_by_trace[span.context.trace_id].add(span_component)
        complete = sum(
            CRITICAL_COMPONENTS.issubset(components) for components in components_by_trace.values()
        )
        coverage = complete / len(components_by_trace)

        assert len(components_by_trace) == 20
        assert coverage >= 0.99
    finally:
        runtime.shutdown()


def test_exception_text_never_reaches_span_log_or_metric(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exporter = InMemorySpanExporter()
    runtime = _runtime(exporter)
    try:
        with (
            pytest.raises(RuntimeError),
            runtime.operation(component="model", operation="invoke"),
        ):
            raise RuntimeError(SENSITIVE_MARKER)
        metrics = runtime.metrics_payload().decode("utf-8")
        logs = capsys.readouterr().err
        spans = exporter.get_finished_spans()
        serialized_spans = json.dumps(
            [dict(span.attributes or {}) for span in spans],
            ensure_ascii=False,
            default=str,
        )

        assert SENSITIVE_MARKER not in metrics
        assert SENSITIVE_MARKER not in logs
        assert SENSITIVE_MARKER not in serialized_spans
        assert all(not span.events for span in spans)
    finally:
        runtime.shutdown()


def test_prometheus_rules_cover_latency_error_backlog_degradation_and_sse() -> None:
    document = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    rules = document["groups"][0]["rules"]
    names = {rule["alert"] for rule in rules}
    expressions = "\n".join(str(rule["expr"]) for rule in rules)

    assert names == {
        "AiPlatformDependencyUnavailable",
        "AiPlatformHttpErrorRatioHigh",
        "AiPlatformHttpP95LatencyHigh",
        "AiPlatformWorkerTaskFailure",
        "AiPlatformOutboxBacklogOldest",
        "AiPlatformOutboxDeadLetter",
        "AiPlatformSseNotificationFailure",
    }
    assert "ai_platform_outbox_oldest_pending_age_seconds > 300" in expressions
    assert "ai_platform_dependency_health" in expressions
    assert "ai_platform_sse_notifications_total" in expressions
    assert all(
        forbidden not in expressions
        for forbidden in ("workspace_id", "user_id", "document_id", "trace_id")
    )


def test_prometheus_multiprocess_payload_contains_worker_metrics(tmp_path: Path) -> None:
    script = f"""
from ai_platform_backend.observability.runtime import ObservabilityRuntime
runtime = ObservabilityRuntime(
    service_name="ai-platform-worker",
    environment="test",
    field_registry_path={str(REGISTRY_PATH)!r},
    metrics_process_prefix="worker-control",
)
with runtime.task(task_name="synthetic.task.v1", queue="platform.control"):
    pass
runtime.outbox_pending.labels(**runtime.common_labels).set(3)
print(runtime.metrics_payload().decode("utf-8"))
runtime.shutdown()
"""
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    environment["PYTHONPATH"] = str(ROOT / "packages" / "backend" / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert 'task_name="synthetic.task.v1"' in result.stdout
    assert "ai_platform_outbox_pending_events" in result.stdout
    assert " 3.0" in result.stdout
