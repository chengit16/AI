"""实现工作空间生命周期事实、导出读取和受限清理的 PostgreSQL Adapter。"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from ai_platform_backend.database import SCHEMA_TOKEN
from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent
from ai_platform_backend.integration.persistence import (
    audit_records,
    consumer_receipts,
    outbox_events,
    outbox_replay_requests,
)
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, MetaData, Table, delete, insert, inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.lifecycle.domain.compliance import (
    LifecycleComplianceWriteConflictError,
    LifecycleOperationBlockedError,
)
from ai_platform_api.modules.lifecycle.domain.models import (
    DeletionCertificate,
    LifecycleExport,
    LifecyclePurge,
    RetentionRun,
)
from ai_platform_api.modules.lifecycle.domain.ports import (
    LifecycleRegistryDriftError,
    LifecycleWriteConflictError,
)
from ai_platform_api.modules.lifecycle.domain.registry import WorkspaceTableRegistry
from ai_platform_api.modules.lifecycle.infrastructure.compliance import (
    record_lifecycle_compliance_decision,
)
from ai_platform_api.persistence.tables import (
    ingestion_job_attempts,
    lifecycle_deletion_certificates,
    lifecycle_export_records,
    lifecycle_legal_hold_releases,
    lifecycle_legal_holds,
    lifecycle_purge_requests,
    lifecycle_regulatory_policy_versions,
    lifecycle_retention_runs,
    stream_events,
    workspace_usage_records,
    workspaces,
)

SessionFactory = sessionmaker[Session]


class SqlAlchemyWorkspaceLifecycleStore:
    """以短事务维护生命周期状态，并只在清理事务设置受限 GUC。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def assert_registry_coverage(self, registry: WorkspaceTableRegistry) -> None:
        """确认全部直接工作空间表及登记子表与真实 PostgreSQL Schema 一致。"""

        with self._session_factory() as session:
            connection = session.connection()
            schema = _actual_schema(connection)
            inspector = inspect(connection)
            direct = {
                table_name
                for table_name in inspector.get_table_names(schema=schema)
                if any(
                    column["name"] == "workspace_id"
                    for column in inspector.get_columns(table_name, schema=schema)
                )
            }
            registered = {item.table for item in registry.tables}
            dependent = {item.table for item in registry.dependent_tables}
            existing = set(inspector.get_table_names(schema=schema))
        if direct != registered or not dependent.issubset(existing):
            raise LifecycleRegistryDriftError(
                f"生命周期表分类漂移: missing={sorted(direct - registered)}, "
                f"stale={sorted(registered - direct)}, dependent={sorted(dependent - existing)}"
            )

    def workspace_name(self, workspace_id: UUID) -> str | None:
        with self._session_factory() as session:
            return session.scalar(
                select(workspaces.c.name).where(workspaces.c.workspace_id == workspace_id)
            )

    def read_export_rows(
        self,
        workspace_id: UUID,
        registry: WorkspaceTableRegistry,
    ) -> dict[str, tuple[dict[str, object], ...]]:
        """在一个一致快照内读取所有可导出直接表与依赖子表。"""

        with self._session_factory() as session, session.begin():
            connection = session.connection()
            schema = _actual_schema(connection)
            tables = {
                policy.table: _reflect_table(connection, schema, policy.table)
                for policy in registry.export_tables()
            }
            result = {
                policy.table: _read_direct_rows(
                    session,
                    tables[policy.table],
                    workspace_id,
                    policy.excluded_columns,
                )
                for policy in registry.export_tables()
            }
            for policy in registry.dependent_tables:
                if not policy.export:
                    continue
                child = _reflect_table(connection, schema, policy.table)
                parent = tables[policy.parent_table]
                result[policy.table] = _read_dependent_rows(
                    session,
                    child,
                    parent,
                    workspace_id,
                    policy.local_column,
                    policy.parent_column,
                    policy.excluded_columns,
                )
            return dict(sorted(result.items()))

    def get_export(self, workspace_id: UUID, idempotency_key: str) -> LifecycleExport | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(lifecycle_export_records).where(
                        lifecycle_export_records.c.workspace_id == workspace_id,
                        lifecycle_export_records.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .one_or_none()
            )
            return _export(row) if row is not None else None

    def add_export(self, export: LifecycleExport) -> LifecycleExport:
        """原子认领幂等键；并发失败方读取获胜请求而不重复生成导出包。"""

        with self._session_factory() as session, session.begin():
            # 1. 在同一工作空间锁内先冻结导出裁决，导出权限仍由原调用链独立校验。
            proof = record_lifecycle_compliance_decision(
                session,
                workspace_id=export.workspace_id,
                operation="export",
                operation_id=export.export_id,
                idempotency_key=export.idempotency_key,
                request_hash=export.request_hash,
                actor_id=export.requested_by_account_id,
                occurred_at=export.created_at,
            )
            # 2. 插入使用证明绑定的操作身份，并发失败方随后读取获胜请求。
            row = (
                session.execute(
                    postgresql_insert(lifecycle_export_records)
                    .values(
                        **{
                            **export.__dict__,
                            # 并发失败方复用获胜证明冻结的操作身份，才能命中同一幂等事实。
                            "export_id": proof.operation_id,
                            "compliance_proof_id": proof.compliance_proof_id,
                        }
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            lifecycle_export_records.c.workspace_id,
                            lifecycle_export_records.c.idempotency_key,
                        ]
                    )
                    .returning(lifecycle_export_records)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                row = (
                    session.execute(
                        select(lifecycle_export_records).where(
                            lifecycle_export_records.c.workspace_id == export.workspace_id,
                            lifecycle_export_records.c.idempotency_key == export.idempotency_key,
                        )
                    )
                    .mappings()
                    .one()
                )
            return _export(row)

    def complete_export(
        self,
        export_id: UUID,
        *,
        object_key: str,
        bundle_size_bytes: int,
        bundle_sha256: str,
        object_manifest_sha256: str,
        table_count: int,
        object_count: int,
        completed_at: datetime,
    ) -> LifecycleExport:
        values = {
            "status": "completed",
            "object_key": object_key,
            "bundle_size_bytes": bundle_size_bytes,
            "bundle_sha256": bundle_sha256,
            "object_manifest_sha256": object_manifest_sha256,
            "table_count": table_count,
            "object_count": object_count,
            "completed_at": completed_at,
        }
        with self._session_factory() as session, session.begin():
            row = (
                session.execute(
                    update(lifecycle_export_records)
                    .where(
                        lifecycle_export_records.c.export_id == export_id,
                        lifecycle_export_records.c.status == "running",
                    )
                    .values(**values)
                    .returning(lifecycle_export_records)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LifecycleWriteConflictError
            return _export(row)

    def fail_export(self, export_id: UUID, error_code: str, completed_at: datetime) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(
                update(lifecycle_export_records)
                .where(
                    lifecycle_export_records.c.export_id == export_id,
                    lifecycle_export_records.c.status == "running",
                )
                .values(status="failed", error_code=error_code, completed_at=completed_at)
            )

    def get_purge(self, workspace_id: UUID, idempotency_key: str) -> LifecyclePurge | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(lifecycle_purge_requests).where(
                        lifecycle_purge_requests.c.workspace_id == workspace_id,
                        lifecycle_purge_requests.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .one_or_none()
            )
            return _purge(row) if row is not None else None

    def add_purge(self, purge: LifecyclePurge) -> LifecyclePurge:
        """原子认领清理请求，避免并发请求绕过幂等键唯一事实。"""

        blocked = None
        row: RowMapping | None = None
        with self._session_factory() as session, session.begin():
            # 1. 先冻结法规策略与活动保留集合，阻断证明也必须作为不可变事实提交。
            try:
                proof = record_lifecycle_compliance_decision(
                    session,
                    workspace_id=purge.workspace_id,
                    operation="purge",
                    operation_id=purge.purge_request_id,
                    idempotency_key=purge.idempotency_key,
                    request_hash=purge.request_hash,
                    actor_id=purge.requested_by_account_id,
                    occurred_at=purge.created_at,
                )
            except LifecycleComplianceWriteConflictError as error:
                raise LifecycleWriteConflictError from error
            if proof.decision != "allowed":
                blocked = proof
            else:
                # 2. 允许时使用证明绑定的请求身份原子认领，避免并发随机 ID 脱钩。
                row = (
                    session.execute(
                        postgresql_insert(lifecycle_purge_requests)
                        .values(
                            **{
                                **purge.__dict__,
                                # 证明先于请求事实裁决，复用其操作身份可消除并发随机 ID 竞态。
                                "purge_request_id": proof.operation_id,
                                "compliance_proof_id": proof.compliance_proof_id,
                            }
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                lifecycle_purge_requests.c.workspace_id,
                                lifecycle_purge_requests.c.idempotency_key,
                            ]
                        )
                        .returning(lifecycle_purge_requests)
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    row = (
                        session.execute(
                            select(lifecycle_purge_requests).where(
                                lifecycle_purge_requests.c.workspace_id == purge.workspace_id,
                                lifecycle_purge_requests.c.idempotency_key == purge.idempotency_key,
                            )
                        )
                        .mappings()
                        .one()
                    )
        if blocked is not None:
            raise LifecycleOperationBlockedError(blocked)
        if row is None:
            raise LifecycleWriteConflictError
        return _purge(row)

    def purge_database(
        self,
        purge_request_id: UUID,
        registry: WorkspaceTableRegistry,
        now: datetime,
    ) -> LifecyclePurge:
        """先清除无空间列子表，再按真实外键顺序清除业务事实。"""

        # 1. 锁定清理请求并只在当前事务开启旁路，普通事务仍受不可变触发器保护。
        with self._session_factory() as session, session.begin():
            row = _locked_purge(session, purge_request_id)
            _lock_compliance_workspace(session, cast(UUID, row["workspace_id"]))
            if _active_legal_hold_ids(session, cast(UUID, row["workspace_id"])):
                raise LifecycleWriteConflictError
            if cast(bool, row["database_cleared"]):
                return _purge(row)
            session.execute(text("SET LOCAL ai_platform.lifecycle_purge = 'on'"))
            session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection = session.connection()
            schema = _actual_schema(connection)
            direct = {
                policy.table: _reflect_table(connection, schema, policy.table)
                for policy in registry.purge_tables()
            }
            counts: dict[str, int] = {}
            # 2. 无空间列子表先按父事实归属删除，再按真实外键拓扑清理直接业务表。
            for policy in registry.dependent_tables:
                if not policy.purge:
                    continue
                child = _reflect_table(connection, schema, policy.table)
                parent = direct[policy.parent_table]
                statement = delete(child).where(
                    child.c[policy.local_column].in_(
                        select(parent.c[policy.parent_column]).where(
                            parent.c.workspace_id == row["workspace_id"]
                        )
                    )
                )
                counts[policy.table] = _rowcount(session.execute(statement))
            for table_name in _purge_order(connection, schema, direct):
                table = direct[table_name]
                counts[table_name] = _rowcount(
                    session.execute(
                        delete(table).where(table.c.workspace_id == row["workspace_id"])
                    )
                )
            updated = (
                session.execute(
                    update(lifecycle_purge_requests)
                    .where(lifecycle_purge_requests.c.purge_request_id == purge_request_id)
                    .values(
                        database_cleared=True,
                        deleted_table_counts=dict(sorted(counts.items())),
                        status="pending",
                        last_error_code=None,
                        updated_at=now,
                    )
                    .returning(lifecycle_purge_requests)
                )
                .mappings()
                .one()
            )
            return _purge(updated)

    def mark_purge_external_step(
        self,
        purge_request_id: UUID,
        *,
        step: str,
        count: int,
        now: datetime,
    ) -> LifecyclePurge:
        if step not in {"objects", "cache"}:
            raise ValueError("未知生命周期外部清理步骤")
        values: dict[str, object] = {
            "status": "pending",
            "last_error_code": None,
            "updated_at": now,
        }
        if step == "objects":
            values.update(objects_cleared=True, deleted_object_count=count)
        else:
            values.update(cache_cleared=True, deleted_cache_key_count=count)
        with self._session_factory() as session, session.begin():
            row = (
                session.execute(
                    update(lifecycle_purge_requests)
                    .where(lifecycle_purge_requests.c.purge_request_id == purge_request_id)
                    .values(**values)
                    .returning(lifecycle_purge_requests)
                )
                .mappings()
                .one()
            )
            return _purge(row)

    def mark_purge_retryable(
        self,
        purge_request_id: UUID,
        error_code: str,
        now: datetime,
    ) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(
                update(lifecycle_purge_requests)
                .where(lifecycle_purge_requests.c.purge_request_id == purge_request_id)
                .values(status="retryable", last_error_code=error_code, updated_at=now)
            )

    def finalize_purge(
        self,
        purge_request_id: UUID,
        registry_version: int,
        context: RequestContext,
        now: datetime,
    ) -> tuple[LifecyclePurge, DeletionCertificate]:
        """同事务写入完成状态、删除证明、审计与最小 Outbox 完成事件。"""

        # 1. 锁定请求并优先返回既有证明，重复完成不能生成第二份证书或事件。
        with self._session_factory() as session, session.begin():
            row = _locked_purge(session, purge_request_id)
            existing = (
                session.execute(
                    select(lifecycle_deletion_certificates).where(
                        lifecycle_deletion_certificates.c.purge_request_id == purge_request_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                return _purge(row), _certificate(existing)
            if not all(
                cast(bool, row[field])
                for field in ("database_cleared", "objects_cleared", "cache_cleared")
            ):
                raise LifecycleWriteConflictError
            # 2. 证明摘要只覆盖删除计数和标识；审计与 Outbox 不复制任何原业务正文。
            result = {
                "workspace_id": str(row["workspace_id"]),
                "purge_request_id": str(purge_request_id),
                "registry_version": registry_version,
                "deleted_table_counts": row["deleted_table_counts"],
                "deleted_object_count": row["deleted_object_count"],
                "deleted_cache_key_count": row["deleted_cache_key_count"],
                "completed_at": now.isoformat(),
            }
            result_sha256 = _document_hash(result)
            certificate_id = uuid4()
            certificate_row = (
                session.execute(
                    insert(lifecycle_deletion_certificates)
                    .values(
                        certificate_id=certificate_id,
                        workspace_id=row["workspace_id"],
                        purge_request_id=purge_request_id,
                        registry_version=registry_version,
                        deleted_table_counts=row["deleted_table_counts"],
                        deleted_object_count=row["deleted_object_count"],
                        deleted_cache_key_count=row["deleted_cache_key_count"],
                        result_sha256=result_sha256,
                        completed_at=now,
                    )
                    .returning(lifecycle_deletion_certificates)
                )
                .mappings()
                .one()
            )
            SqlAlchemyAuditWriter(session).add(
                AuditRecord(
                    audit_id=uuid4(),
                    workspace_id=cast(UUID, row["workspace_id"]),
                    actor_id=context.actor_id,
                    user_id=context.user_id,
                    action="workspace.lifecycle.purge",
                    resource_type="lifecycle_purge_request",
                    resource_id=purge_request_id,
                    outcome="succeeded",
                    occurred_at=now,
                    request_id=context.request_id,
                    trace_id=context.trace.trace_id,
                    traceparent=context.trace.traceparent,
                    authorization=context.audit_authorization,
                    attributes={
                        "certificate_id": str(certificate_id),
                        "result_sha256": result_sha256,
                        "reason_code": row["reason_code"],
                    },
                )
            )
            SqlAlchemyOutboxWriter(session).add(
                IntegrationEvent(
                    event_id=uuid4(),
                    event_type="workspace.lifecycle.purge_completed",
                    workspace_id=cast(UUID, row["workspace_id"]),
                    aggregate_id=purge_request_id,
                    aggregate_version=1,
                    occurred_at=now,
                    trace_id=context.trace.trace_id,
                    traceparent=context.trace.traceparent,
                    actor_id=context.actor_id,
                    user_id=context.user_id,
                    request_id=context.request_id,
                    payload={
                        "certificate_id": str(certificate_id),
                        "result_sha256": result_sha256,
                    },
                )
            )
            # 3. 证明、审计、完成事件和请求终态在同一事务提交，避免出现不可追溯的完成状态。
            completed = (
                session.execute(
                    update(lifecycle_purge_requests)
                    .where(lifecycle_purge_requests.c.purge_request_id == purge_request_id)
                    .values(
                        status="completed", last_error_code=None, updated_at=now, completed_at=now
                    )
                    .returning(lifecycle_purge_requests)
                )
                .mappings()
                .one()
            )
            return _purge(completed), _certificate(certificate_row)

    def get_retention_run(self, workspace_id: UUID, idempotency_key: str) -> RetentionRun | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(lifecycle_retention_runs).where(
                        lifecycle_retention_runs.c.workspace_id == workspace_id,
                        lifecycle_retention_runs.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .one_or_none()
            )
            return _retention(row) if row is not None else None

    def add_retention_run(self, run: RetentionRun) -> RetentionRun:
        """原子认领保留期运行，使并发调用共同等待同一个数据库事实。"""

        blocked = None
        row: RowMapping | None = None
        with self._session_factory() as session, session.begin():
            # 1. 先冻结策略与法律保留裁决，同一幂等键只能绑定一个证明。
            try:
                proof = record_lifecycle_compliance_decision(
                    session,
                    workspace_id=run.workspace_id,
                    operation="retention",
                    operation_id=run.retention_run_id,
                    idempotency_key=run.idempotency_key,
                    request_hash=run.request_hash,
                    actor_id=run.requested_by_account_id,
                    occurred_at=run.created_at,
                )
            except LifecycleComplianceWriteConflictError as error:
                raise LifecycleWriteConflictError from error
            if proof.decision != "allowed" or proof.regulatory_policy_id is None:
                blocked = proof
            else:
                # 2. 运行身份复用证明中的获胜 ID，使并发请求命中同一运行事实。
                row = (
                    session.execute(
                        postgresql_insert(lifecycle_retention_runs)
                        .values(
                            **{
                                **run.__dict__,
                                # 同一幂等键的第二个请求必须引用首个证明绑定的运行身份。
                                "retention_run_id": proof.operation_id,
                                "regulatory_policy_id": proof.regulatory_policy_id,
                                "compliance_proof_id": proof.compliance_proof_id,
                            }
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                lifecycle_retention_runs.c.workspace_id,
                                lifecycle_retention_runs.c.idempotency_key,
                            ]
                        )
                        .returning(lifecycle_retention_runs)
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    row = (
                        session.execute(
                            select(lifecycle_retention_runs).where(
                                lifecycle_retention_runs.c.workspace_id == run.workspace_id,
                                lifecycle_retention_runs.c.idempotency_key == run.idempotency_key,
                            )
                        )
                        .mappings()
                        .one()
                    )
        if blocked is not None:
            raise LifecycleOperationBlockedError(blocked)
        if row is None:
            raise LifecycleWriteConflictError
        return _retention(row)

    def execute_retention(
        self,
        retention_run_id: UUID,
        context: RequestContext,
        now: datetime,
    ) -> RetentionRun:
        """按冻结边界删除到期事实，并为本次运行保存计数摘要。"""

        with self._session_factory() as session, session.begin():
            # 1. 锁定获胜运行与工作空间，重新确认没有并发激活的法律保留。
            run_row = (
                session.execute(
                    select(lifecycle_retention_runs)
                    .where(lifecycle_retention_runs.c.retention_run_id == retention_run_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if run_row["status"] == "completed":
                return _retention(run_row)
            workspace_id = cast(UUID, run_row["workspace_id"])
            _lock_compliance_workspace(session, workspace_id)
            if _active_legal_hold_ids(session, workspace_id):
                raise LifecycleWriteConflictError
            policy_row = (
                session.execute(
                    select(lifecycle_regulatory_policy_versions).where(
                        lifecycle_regulatory_policy_versions.c.regulatory_policy_id
                        == run_row["regulatory_policy_id"],
                        lifecycle_regulatory_policy_versions.c.workspace_id == workspace_id,
                    )
                )
                .mappings()
                .one()
            )
            periods = dict(cast(Mapping[str, int], policy_row["retention_period_days"]))
            cutoffs = {key: now - timedelta(days=periods[key]) for key in periods}
            session.execute(text("SET LOCAL ai_platform.lifecycle_purge = 'on'"))
            # 2. 在受限旁路事务内删除到期事实，并把计数摘要、审计和终态一次提交。
            counts = _delete_expired_records(session, workspace_id, cutoffs)
            result = {
                "workspace_id": str(workspace_id),
                "retention_run_id": str(retention_run_id),
                "cutoffs": {key: value.isoformat() for key, value in cutoffs.items()},
                "deleted_table_counts": counts,
            }
            result_sha256 = _document_hash(result)
            # 3. 删除摘要、审计和运行终态同事务提交，不持久化被删除的业务正文。
            SqlAlchemyAuditWriter(session).add(
                AuditRecord(
                    audit_id=uuid4(),
                    workspace_id=workspace_id,
                    actor_id=context.actor_id,
                    user_id=context.user_id,
                    action="workspace.lifecycle.retention.execute",
                    resource_type="lifecycle_retention_run",
                    resource_id=retention_run_id,
                    outcome="succeeded",
                    occurred_at=now,
                    request_id=context.request_id,
                    trace_id=context.trace.trace_id,
                    traceparent=context.trace.traceparent,
                    authorization=context.audit_authorization,
                    attributes={"result_sha256": result_sha256},
                )
            )
            completed = (
                session.execute(
                    update(lifecycle_retention_runs)
                    .where(lifecycle_retention_runs.c.retention_run_id == retention_run_id)
                    .values(
                        status="completed",
                        cutoffs=result["cutoffs"],
                        deleted_table_counts=counts,
                        result_sha256=result_sha256,
                        completed_at=now,
                    )
                    .returning(lifecycle_retention_runs)
                )
                .mappings()
                .one()
            )
            return _retention(completed)


def _delete_expired_records(
    session: Session,
    workspace_id: UUID,
    cutoffs: Mapping[str, datetime],
) -> dict[str, int]:
    """按外键与不可变触发器依赖顺序执行冻结保留策略。"""

    # 1. Outbox 子事实必须先于源事件删除，已发布与死信事件分别执行 30/90 天边界。
    published_ids = select(outbox_events.c.event_id).where(
        outbox_events.c.workspace_id == workspace_id,
        outbox_events.c.status == "published",
        outbox_events.c.published_at <= cutoffs["published_outbox"],
    )
    dead_ids = select(outbox_events.c.event_id).where(
        outbox_events.c.workspace_id == workspace_id,
        outbox_events.c.status == "dead_letter",
        outbox_events.c.occurred_at <= cutoffs["attempts_and_dead_letters"],
    )
    expired_event_ids = published_ids.union_all(dead_ids)
    counts = {
        "consumer_receipts": _rowcount(
            session.execute(
                delete(consumer_receipts).where(consumer_receipts.c.event_id.in_(expired_event_ids))
            )
        ),
        "outbox_replay_requests": _rowcount(
            session.execute(
                delete(outbox_replay_requests).where(
                    outbox_replay_requests.c.workspace_id == workspace_id,
                    outbox_replay_requests.c.event_id.in_(expired_event_ids),
                )
            )
        ),
    }
    counts["outbox_events"] = _rowcount(
        session.execute(
            delete(outbox_events).where(
                outbox_events.c.workspace_id == workspace_id,
                outbox_events.c.event_id.in_(expired_event_ids),
            )
        )
    )
    # 2. SSE、Attempt 和最小留存事实按各自时间列删除，达到截止时间的记录计入本次证明。
    counts["stream_events"] = _rowcount(
        session.execute(
            delete(stream_events).where(
                stream_events.c.workspace_id == workspace_id,
                stream_events.c.occurred_at <= cutoffs["stream_events"],
            )
        )
    )
    counts["ingestion_job_attempts"] = _rowcount(
        session.execute(
            delete(ingestion_job_attempts).where(
                ingestion_job_attempts.c.workspace_id == workspace_id,
                ingestion_job_attempts.c.completed_at <= cutoffs["attempts_and_dead_letters"],
            )
        )
    )
    counts["audit_records"] = _rowcount(
        session.execute(
            delete(audit_records).where(
                audit_records.c.workspace_id == workspace_id,
                audit_records.c.occurred_at <= cutoffs["minimum_records"],
            )
        )
    )
    counts["workspace_usage_records"] = _rowcount(
        session.execute(
            delete(workspace_usage_records).where(
                workspace_usage_records.c.workspace_id == workspace_id,
                workspace_usage_records.c.occurred_at <= cutoffs["minimum_records"],
            )
        )
    )
    counts["lifecycle_deletion_certificates"] = _rowcount(
        session.execute(
            delete(lifecycle_deletion_certificates).where(
                lifecycle_deletion_certificates.c.workspace_id == workspace_id,
                lifecycle_deletion_certificates.c.completed_at <= cutoffs["minimum_records"],
            )
        )
    )
    return dict(sorted(counts.items()))


def _read_direct_rows(
    session: Session,
    table: Table,
    workspace_id: UUID,
    excluded_columns: frozenset[str],
) -> tuple[dict[str, object], ...]:
    columns = tuple(column for column in table.c if column.name not in excluded_columns)
    statement = select(*columns).where(table.c.workspace_id == workspace_id)
    if table.primary_key.columns:
        statement = statement.order_by(*table.primary_key.columns)
    return tuple(_export_row(row) for row in session.execute(statement).mappings())


def _read_dependent_rows(
    session: Session,
    child: Table,
    parent: Table,
    workspace_id: UUID,
    local_column: str,
    parent_column: str,
    excluded_columns: frozenset[str],
) -> tuple[dict[str, object], ...]:
    columns = tuple(column for column in child.c if column.name not in excluded_columns)
    statement = (
        select(*columns)
        .select_from(child.join(parent, child.c[local_column] == parent.c[parent_column]))
        .where(parent.c.workspace_id == workspace_id)
    )
    if child.primary_key.columns:
        statement = statement.order_by(*child.primary_key.columns)
    return tuple(_export_row(row) for row in session.execute(statement).mappings())


def _export_row(row: RowMapping) -> dict[str, object]:
    return {str(key): _export_value(value) for key, value in sorted(row.items())}


def _export_value(value: Any) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        return {str(key): _export_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_export_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _export_value(value.tolist())
    raise TypeError(f"导出遇到不支持的数据库值类型: {type(value).__name__}")


def _purge_order(
    connection: Connection,
    schema: str,
    tables: dict[str, Table],
) -> tuple[str, ...]:
    """从真实外键计算叶子优先顺序，循环依赖会明确阻断清理。"""

    inspector = inspect(connection)
    remaining = set(tables)
    dependencies = {
        name: {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys(name, schema=schema)
            if foreign_key.get("referred_table") in remaining
            and not cast(dict[str, object], foreign_key.get("options") or {}).get("deferrable")
        }
        for name in remaining
    }
    result: list[str] = []
    while remaining:
        parents_with_children = {
            parent
            for child in remaining
            for parent in dependencies[child]
            if parent in remaining and parent != child
        }
        leaves = sorted(remaining - parents_with_children)
        if not leaves:
            raise LifecycleRegistryDriftError("业务表外键形成无法安全清理的循环")
        result.extend(leaves)
        remaining.difference_update(leaves)
    return tuple(result)


def _actual_schema(connection: Connection) -> str:
    options = connection.get_execution_options()
    schema_map = cast(dict[str, str] | None, options.get("schema_translate_map"))
    return schema_map.get(SCHEMA_TOKEN, "public") if schema_map is not None else "public"


def _reflect_table(connection: Connection, schema: str, table_name: str) -> Table:
    return Table(table_name, MetaData(), schema=schema, autoload_with=connection)


def _locked_purge(session: Session, purge_request_id: UUID) -> RowMapping:
    return (
        session.execute(
            select(lifecycle_purge_requests)
            .where(lifecycle_purge_requests.c.purge_request_id == purge_request_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )


def _rowcount(result: Any) -> int:
    return max(0, int(cast(CursorResult[Any], result).rowcount or 0))


def _document_hash(value: object) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _lock_compliance_workspace(session: Session, workspace_id: UUID) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:workspace_key, 509))"),
        {"workspace_key": str(workspace_id)},
    )


def _active_legal_hold_ids(session: Session, workspace_id: UUID) -> tuple[UUID, ...]:
    return tuple(
        session.scalars(
            select(lifecycle_legal_holds.c.legal_hold_id)
            .outerjoin(
                lifecycle_legal_hold_releases,
                (
                    lifecycle_legal_hold_releases.c.workspace_id
                    == lifecycle_legal_holds.c.workspace_id
                )
                & (
                    lifecycle_legal_hold_releases.c.legal_hold_id
                    == lifecycle_legal_holds.c.legal_hold_id
                ),
            )
            .where(
                lifecycle_legal_holds.c.workspace_id == workspace_id,
                lifecycle_legal_hold_releases.c.release_id.is_(None),
            )
        )
    )


def _export(row: RowMapping) -> LifecycleExport:
    return LifecycleExport(**dict(row))


def _purge(row: RowMapping) -> LifecyclePurge:
    values = dict(row)
    values["deleted_table_counts"] = {
        str(key): int(value) for key, value in values["deleted_table_counts"].items()
    }
    return LifecyclePurge(**values)


def _certificate(row: RowMapping) -> DeletionCertificate:
    values = dict(row)
    values["deleted_table_counts"] = {
        str(key): int(value) for key, value in values["deleted_table_counts"].items()
    }
    return DeletionCertificate(**values)


def _retention(row: RowMapping) -> RetentionRun:
    values = dict(row)
    values["cutoffs"] = {str(key): str(value) for key, value in values["cutoffs"].items()}
    values["deleted_table_counts"] = {
        str(key): int(value) for key, value in values["deleted_table_counts"].items()
    }
    return RetentionRun(**values)
