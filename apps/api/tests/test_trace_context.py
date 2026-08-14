"""验证 HTTP Trace 建立、延续和响应传播。"""

import re

from ai_platform_api.common.trace import TRACEPARENT_PATTERN, TraceContext
from ai_platform_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_valid_traceparent_continues_trace_with_new_span() -> None:
    parent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"

    trace = TraceContext.continue_from(parent)

    assert trace.trace_id == "0123456789abcdef0123456789abcdef"
    assert trace.span_id != "0123456789abcdef"
    assert trace.trace_flags == "01"
    assert TRACEPARENT_PATTERN.fullmatch(trace.traceparent)


def test_invalid_traceparent_starts_new_trace() -> None:
    trace = TraceContext.continue_from("00-invalid-traceparent")

    assert len(trace.trace_id) == 32
    assert int(trace.trace_id, 16) != 0
    assert len(trace.span_id) == 16
    assert int(trace.span_id, 16) != 0


def test_http_response_returns_trusted_trace_and_request_id() -> None:
    request_id = "10000000-0000-4000-8000-000000000001"
    parent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"

    response = client.get(
        "/api/v1/health/live",
        headers={"traceparent": parent, "x-request-id": request_id},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id
    assert response.headers["traceparent"].split("-")[1] == parent.split("-")[1]
    assert response.headers["traceparent"].split("-")[2] != parent.split("-")[2]
    assert re.fullmatch(TRACEPARENT_PATTERN, response.headers["traceparent"])


def test_http_response_replaces_untrusted_identifiers() -> None:
    response = client.get(
        "/api/v1/health/live",
        headers={"traceparent": "invalid", "x-request-id": "invalid"},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "invalid"
    assert TRACEPARENT_PATTERN.fullmatch(response.headers["traceparent"])
