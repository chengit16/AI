"""验证 P2-08 生命周期 Router 的危险操作确认与响应协议。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.lifecycle.api.routes import lifecycle_service, router
from ai_platform_api.modules.lifecycle.application.service import WorkspaceLifecycleService
from ai_platform_api.modules.lifecycle.domain.models import (
    DeletionCertificate,
    LifecycleExport,
    LifecyclePurge,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000208")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000208")
EXPORT_ID = UUID("70000000-0000-4000-8000-000000000208")
PURGE_ID = UUID("71000000-0000-4000-8000-000000000208")
CERTIFICATE_ID = UUID("72000000-0000-4000-8000-000000000208")
NOW = datetime(2026, 8, 15, 8, 30, tzinfo=UTC)
TRACE = TraceContext.continue_from("00-9123456789abcdef0123456789abcdef-9123456789abcdef-01")


class StubLifecycleService:
    """记录危险操作参数并返回不含原业务正文的合成结果。"""

    def __init__(self) -> None:
        self.purge_arguments: tuple[str, str, str] | None = None

    def export_workspace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> LifecycleExport:
        assert context.workspace_id == workspace_id
        return LifecycleExport(
            EXPORT_ID,
            workspace_id,
            idempotency_key,
            "1" * 64,
            "completed",
            1,
            f"workspaces/{workspace_id}/exports/{EXPORT_ID}.zip",
            128,
            "2" * 64,
            "3" * 64,
            4,
            1,
            ACCOUNT_ID,
            context.request_id,
            context.trace.trace_id,
            context.trace.traceparent,
            NOW,
            NOW,
            None,
        )

    def purge_workspace_business_data(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        confirmed_workspace_name: str,
        reason_code: str,
    ) -> tuple[LifecyclePurge, DeletionCertificate]:
        assert context.workspace_id == workspace_id
        self.purge_arguments = (idempotency_key, confirmed_workspace_name, reason_code)
        purge = LifecyclePurge(
            PURGE_ID,
            workspace_id,
            idempotency_key,
            "4" * 64,
            reason_code,
            confirmed_workspace_name,
            "completed",
            True,
            True,
            True,
            {"workspace_resources": 1},
            1,
            1,
            None,
            ACCOUNT_ID,
            context.request_id,
            context.trace.trace_id,
            context.trace.traceparent,
            NOW,
            NOW,
            NOW,
        )
        certificate = DeletionCertificate(
            CERTIFICATE_ID,
            workspace_id,
            PURGE_ID,
            1,
            purge.deleted_table_counts,
            1,
            1,
            "5" * 64,
            NOW,
        )
        return purge, certificate


def _context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session",
    )


def lifecycle_client() -> tuple[TestClient, StubLifecycleService]:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    stub = StubLifecycleService()
    application.dependency_overrides[trusted_request_context] = _context
    application.dependency_overrides[lifecycle_service] = lambda: cast(
        WorkspaceLifecycleService,
        stub,
    )
    return TestClient(application), stub


def test_export_forwards_header_idempotency_and_returns_hashes() -> None:
    client, _ = lifecycle_client()

    with client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/lifecycle/exports",
            headers={"Idempotency-Key": "synthetic-export-0208"},
        )

    assert response.status_code == 200
    assert response.json()["export_id"] == str(EXPORT_ID)
    assert response.json()["bundle_sha256"] == "2" * 64
    assert "secret" not in response.text.lower()


def test_purge_requires_strict_confirmation_body() -> None:
    client, stub = lifecycle_client()
    path = f"/api/v1/workspaces/{WORKSPACE_ID}/lifecycle/purges"
    headers = {"Idempotency-Key": "synthetic-purge-0208"}

    with client:
        response = client.post(
            path,
            headers=headers,
            json={
                "confirmed_workspace_name": "合成生命周期空间",
                "reason_code": "OWNER_REQUEST",
            },
        )
        rejected = client.post(
            path,
            headers=headers,
            json={
                "confirmed_workspace_name": "合成生命周期空间",
                "reason_code": "OWNER_REQUEST",
                "force": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["certificate"]["result_sha256"] == "5" * 64
    assert stub.purge_arguments == (
        "synthetic-purge-0208",
        "合成生命周期空间",
        "OWNER_REQUEST",
    )
    assert rejected.status_code == 422
