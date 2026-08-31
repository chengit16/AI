"""验证 P2-10 在 PostgreSQL 上的权限、菜单、事务、租约和 Worker 闭环。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.operations.application.service import OperationsWorkbenchService
from ai_platform_api.modules.operations.infrastructure.sqlalchemy import (
    SqlAlchemyOperationsWorkbenchUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    audit_records,
    menu_releases,
    outbox_events,
    registered_menu_api_bindings,
    role_permission_grants,
    roles,
    workspace_menu_publications,
)
from ai_platform_backend.indexing.persistence import (
    index_maintenance_requests,
    index_maintenance_runs,
)
from ai_platform_backend.indexing.tokenization import TOKENIZER_VERSION
from ai_platform_worker.modules.indexing.application.commands import (
    IndexMaintenanceCommandProcessor,
)
from ai_platform_worker.modules.indexing.application.maintenance import IndexMaintenanceProcessor
from ai_platform_worker.modules.indexing.infrastructure.embeddings import (
    DeterministicHashEmbeddingAdapter,
)
from ai_platform_worker.modules.indexing.infrastructure.maintenance_sqlalchemy import (
    SqlAlchemyIndexMaintenanceStore,
)
from ai_platform_worker.modules.indexing.infrastructure.requests_sqlalchemy import (
    SqlAlchemyIndexMaintenanceRequestStore,
)
from sqlalchemy import func, select

from tests.integration import test_p1d04_index_versions_postgres as index_support
from tests.integration import test_p207_operations_postgres as operations_support

index_database = index_support.index_database
operations_database = operations_support.operations_database
NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
WORKBENCH_PERMISSIONS = {
    "knowledge.ingestion.cancel",
    "operations.index.inspect",
    "operations.index.rebuild",
    "operations.index.cleanup",
}


def _context(
    account: index_support.RegisteredAccount,
    permission: str,
) -> RequestContext:
    return replace(
        index_support.context(account, account.personal_workspace_id),
        authorized_permission_code=permission,
        authorized_policy_decision_id=uuid4(),
        authorized_policy_version=18,
        authorized_workspace=True,
    )


def _maintenance(harness: index_support.IndexHarness) -> IndexMaintenanceProcessor:
    embedding = DeterministicHashEmbeddingAdapter()
    return IndexMaintenanceProcessor(
        SqlAlchemyIndexMaintenanceStore(harness.sessions),
        max_attempts=3,
        chunker_version="structural-char-v1",
        embedding_model_version=embedding.model_version,
        tokenizer_version=TOKENIZER_VERSION,
    )


def test_owner_keeps_workbench_permissions_after_current_menu_upgrade(
    operations_database: operations_support.OperationsHarness,
) -> None:
    workspace_id = operations_database.historical_workspace_id
    with operations_database.sessions() as session:
        owner_permissions = set(
            session.scalars(
                select(role_permission_grants.c.permission_code)
                .join(roles, roles.c.role_id == role_permission_grants.c.role_id)
                .where(
                    role_permission_grants.c.workspace_id == workspace_id,
                    roles.c.role_id == operations_database.historical_role_id,
                    role_permission_grants.c.permission_code.in_(WORKBENCH_PERMISSIONS),
                )
            )
        )
        snapshot = session.scalar(
            select(menu_releases.c.snapshot)
            .join(
                workspace_menu_publications,
                workspace_menu_publications.c.current_release_id == menu_releases.c.release_id,
            )
            .where(workspace_menu_publications.c.workspace_id == workspace_id)
        )
        registered_bindings = session.scalar(
            select(func.count())
            .select_from(registered_menu_api_bindings)
            .where(
                registered_menu_api_bindings.c.api_resource_id.in_(
                    [UUID(f"81000000-0000-4000-8000-{number:012d}") for number in range(111, 120)]
                )
            )
        )

    assert owner_permissions == WORKBENCH_PERMISSIONS
    assert isinstance(snapshot, dict) and snapshot["registry_version"] == 31
    assert {item["menu_id"] for item in snapshot["menus"]}.issuperset(
        {
            "82000000-0000-4000-8000-000000000194",
            "82000000-0000-4000-8000-000000000195",
            "82000000-0000-4000-8000-000000000196",
            "82000000-0000-4000-8000-000000000197",
        }
    )
    assert registered_bindings == 9


def test_index_request_audit_outbox_and_worker_result_close_in_one_fact_chain(
    index_database: index_support.IndexHarness,
) -> None:
    account = index_support.register(index_database)
    workspace_id = account.personal_workspace_id
    service = OperationsWorkbenchService(
        SqlAlchemyOperationsWorkbenchUnitOfWork(index_database.sessions)
    )

    # 1. API 应用服务原子登记请求、授权审计和最小 Outbox 事件。
    request = service.request_index_maintenance(
        _context(account, "operations.index.inspect"),
        workspace_id=workspace_id,
        command="inspection",
        idempotency_key=f"synthetic:p210:inspection:{uuid4().hex}",
        reason_code="OPERATOR_REQUEST",
        confirmation="RUN_INDEX_INSPECTION",
        requested_at=NOW,
    )
    with index_database.sessions() as session:
        audit_count = session.scalar(
            select(func.count())
            .select_from(audit_records)
            .where(
                audit_records.c.workspace_id == workspace_id,
                audit_records.c.resource_id == request.maintenance_request_id,
                audit_records.c.permission_code == "operations.index.inspect",
            )
        )
        event = session.execute(
            select(outbox_events.c.event_type, outbox_events.c.payload).where(
                outbox_events.c.workspace_id == workspace_id,
                outbox_events.c.aggregate_id == request.maintenance_request_id,
            )
        ).one()
    assert audit_count == 1
    assert event.event_type == "index.maintenance.requested"
    assert event.payload == {
        "maintenance_request_id": str(request.maintenance_request_id),
        "command": "inspection",
    }

    # 2. Worker 使用请求 ID 作为运行 ID；空空间巡检也必须生成可验证完成证据。
    processor = IndexMaintenanceCommandProcessor(
        SqlAlchemyIndexMaintenanceRequestStore(index_database.sessions),
        _maintenance(index_database),
        worker_id="synthetic-p210-worker",
        lease_seconds=30,
    )
    result = processor.run_batch(limit=10, now=NOW + timedelta(seconds=1))
    with index_database.sessions() as session:
        request_row = session.execute(
            select(
                index_maintenance_requests.c.status,
                index_maintenance_requests.c.attempt_count,
                index_maintenance_requests.c.completed_at,
            ).where(
                index_maintenance_requests.c.maintenance_request_id
                == request.maintenance_request_id
            )
        ).one()
        run_row = session.execute(
            select(
                index_maintenance_runs.c.run_kind,
                index_maintenance_runs.c.scanned_document_count,
                index_maintenance_runs.c.result_digest,
            ).where(index_maintenance_runs.c.maintenance_run_id == request.maintenance_request_id)
        ).one()
    assert result.claimed == result.completed == 1
    assert tuple(request_row[:2]) == ("completed", 1)
    assert request_row.completed_at is not None
    assert run_row.run_kind == "inspection" and run_row.scanned_document_count == 0
    assert len(run_row.result_digest) == 64


class FailingMaintenance:
    """模拟维护实现持续失败，验证数据库有限重试而不依赖异常文本。"""

    def cleanup_unrecoverable_chunks(self, maintenance_run_id: UUID, **_: object) -> None:
        raise RuntimeError("synthetic p210 failure")


def test_request_lease_retries_three_times_then_enters_dead_letter(
    index_database: index_support.IndexHarness,
) -> None:
    account = index_support.register(index_database)
    workspace_id = account.personal_workspace_id
    service = OperationsWorkbenchService(
        SqlAlchemyOperationsWorkbenchUnitOfWork(index_database.sessions)
    )
    request = service.request_index_maintenance(
        _context(account, "operations.index.cleanup"),
        workspace_id=workspace_id,
        command="cleanup",
        idempotency_key=f"synthetic:p210:cleanup:{uuid4().hex}",
        reason_code="OPERATOR_REQUEST",
        confirmation="DELETE_UNRECOVERABLE_INDEX_CHUNKS",
        requested_at=NOW + timedelta(minutes=1),
    )
    processor = IndexMaintenanceCommandProcessor(
        SqlAlchemyIndexMaintenanceRequestStore(index_database.sessions),
        cast(IndexMaintenanceProcessor, FailingMaintenance()),
        worker_id="synthetic-p210-failing-worker",
        lease_seconds=30,
        max_attempts=3,
    )

    outcomes = [
        processor.run_batch(limit=1, now=NOW + timedelta(minutes=1, seconds=offset))
        for offset in range(3)
    ]
    with index_database.sessions() as session:
        row = session.execute(
            select(
                index_maintenance_requests.c.status,
                index_maintenance_requests.c.attempt_count,
                index_maintenance_requests.c.last_error_code,
                index_maintenance_requests.c.completed_at,
            ).where(
                index_maintenance_requests.c.maintenance_request_id
                == request.maintenance_request_id
            )
        ).one()

    assert [(item.retried, item.dead_lettered) for item in outcomes] == [(1, 0), (1, 0), (0, 1)]
    assert tuple(row[:3]) == ("dead_letter", 3, "INDEX_MAINTENANCE_RUNTIMEERROR")
    assert row.completed_at is not None
