"""验证 P2-07 运营 Router 的 Payload 隔离与重放协议。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.integration.api.routes import operations_service, router
from ai_platform_api.modules.integration.application.operations import (
    IntegrationOperationsService,
)
from ai_platform_api.modules.integration.domain.operations import (
    OutboxOperationsPage,
    OutboxOperationsRecord,
    OutboxReplayRequest,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000207")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000207")
EVENT_ID = UUID("60000000-0000-4000-8000-000000000207")
NOW = datetime(2026, 8, 15, 7, 30, tzinfo=UTC)
TRACE = TraceContext.continue_from("00-8123456789abcdef0123456789abcdef-8123456789abcdef-01")


class StubOperationsService:
    """仅记录 HTTP 参数并返回包含完整运营元数据的合成领域事实。"""

    def __init__(self) -> None:
        self.replay_arguments: tuple[UUID, UUID, str, str] | None = None

    def list_outbox_events(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        **_: object,
    ) -> OutboxOperationsPage:
        assert context.workspace_id == workspace_id
        return OutboxOperationsPage(
            items=(
                OutboxOperationsRecord(
                    event_id=EVENT_ID,
                    workspace_id=workspace_id,
                    event_type="synthetic.resource.changed",
                    schema_version=1,
                    aggregate_id=uuid4(),
                    aggregate_version=3,
                    occurred_at=NOW,
                    status="dead_letter",
                    attempt_count=5,
                    available_at=NOW,
                    claim_until=None,
                    last_error_code="SYNTHETIC_FAILURE",
                    published_at=None,
                    actor_id=ACCOUNT_ID,
                    user_id=ACCOUNT_ID,
                    request_id=uuid4(),
                    trace_id=TRACE.trace_id,
                    replay_count=0,
                ),
            ),
            next_cursor=None,
        )

    def replay_outbox_event(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        event_id: UUID,
        idempotency_key: str,
        reason_code: str,
    ) -> OutboxReplayRequest:
        assert context.workspace_id == workspace_id
        self.replay_arguments = (
            workspace_id,
            event_id,
            idempotency_key,
            reason_code,
        )
        return OutboxReplayRequest(
            replay_request_id=uuid4(),
            workspace_id=workspace_id,
            event_id=event_id,
            idempotency_key=idempotency_key,
            reason_code=reason_code,
            source_status="dead_letter",
            source_attempt_count=5,
            source_published_at=None,
            source_error_code="SYNTHETIC_FAILURE",
            requested_by_actor_id=ACCOUNT_ID,
            requested_by_user_id=ACCOUNT_ID,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            requested_at=NOW,
        )


def _context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session",
    )


def operations_client() -> tuple[TestClient, StubOperationsService]:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    stub = StubOperationsService()
    application.dependency_overrides[trusted_request_context] = _context
    application.dependency_overrides[operations_service] = lambda: cast(
        IntegrationOperationsService,
        stub,
    )
    return TestClient(application), stub


def test_outbox_http_response_never_contains_payload() -> None:
    client, _ = operations_client()

    with client:
        response = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/operations/outbox-events")

    assert response.status_code == 200
    assert response.json()["items"][0]["event_id"] == str(EVENT_ID)
    assert "payload" not in response.text.lower()


def test_replay_http_contract_is_strict_and_forwards_idempotency() -> None:
    client, stub = operations_client()
    path = f"/api/v1/workspaces/{WORKSPACE_ID}/operations/outbox-events/{EVENT_ID}/replay"

    with client:
        response = client.post(
            path,
            json={
                "idempotency_key": "synthetic-replay-0001",
                "reason_code": "MANUAL_RECOVERY",
            },
        )
        rejected = client.post(
            path,
            json={
                "idempotency_key": "synthetic-replay-0002",
                "reason_code": "MANUAL_RECOVERY",
                "payload": {"secret": "forbidden"},
            },
        )

    assert response.status_code == 200
    assert response.json()["event_id"] == str(EVENT_ID)
    assert stub.replay_arguments == (
        WORKSPACE_ID,
        EVENT_ID,
        "synthetic-replay-0001",
        "MANUAL_RECOVERY",
    )
    assert rejected.status_code == 422
