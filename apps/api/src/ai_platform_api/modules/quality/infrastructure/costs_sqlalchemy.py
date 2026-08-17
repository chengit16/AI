"""使用工作空间复合条件持久化成本窗口、账本和固定组件聚合。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ai_platform_api.modules.quality.domain.costs import (
    CostAmountSource,
    CostAttributionLine,
    CostAttributionReport,
    CostAttributionWindow,
    CostComponent,
    CostEvidenceKind,
    CostLedgerEntry,
    CostOutcome,
    CostReconciliationStatus,
    CostReleaseContext,
    CostSourceKind,
    CostUsageUnit,
    CostVerificationStatus,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    ai_runtime_config_versions,
    cost_attribution_lines,
    cost_attribution_windows,
    cost_ledger_entries,
    services,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyCostAttributionRepository:
    """读取不可变发布身份，并按父、账本、聚合顺序写入成本事实。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_release_context(
        self,
        workspace_id: UUID,
        service_id: UUID,
        agent_release_id: UUID,
    ) -> CostReleaseContext | None:
        row = (
            self._session.execute(
                select(
                    services.c.service_id,
                    agent_releases.c.release_id.label("agent_release_id"),
                    agent_releases.c.runtime_config_version_id,
                    ai_runtime_config_versions.c.content_hash.label("run_configuration_digest"),
                )
                .join(
                    agent_releases,
                    (agent_releases.c.workspace_id == services.c.workspace_id)
                    & (agent_releases.c.agent_id == services.c.agent_id)
                    & (agent_releases.c.release_id == agent_release_id),
                )
                .join(
                    ai_runtime_config_versions,
                    ai_runtime_config_versions.c.runtime_config_version_id
                    == agent_releases.c.runtime_config_version_id,
                )
                .where(
                    services.c.workspace_id == workspace_id,
                    services.c.service_id == service_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return CostReleaseContext(
            service_id=cast(UUID, row["service_id"]),
            agent_release_id=cast(UUID, row["agent_release_id"]),
            runtime_config_version_id=cast(UUID, row["runtime_config_version_id"]),
            run_configuration_digest=cast(str, row["run_configuration_digest"]),
        )

    def lock_window_identity(self, window_identity_digest: str) -> None:
        """事务级锁串行化同一窗口，避免并发账本重复写入。"""

        self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(window_identity_digest, 0)))
        )

    def get_report_by_identity(
        self,
        workspace_id: UUID,
        window_identity_digest: str,
    ) -> CostAttributionReport | None:
        row = (
            self._session.execute(
                select(cost_attribution_windows).where(
                    cost_attribution_windows.c.workspace_id == workspace_id,
                    cost_attribution_windows.c.window_identity_digest == window_identity_digest,
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._report(row) if row is not None else None

    def get_report(
        self,
        workspace_id: UUID,
        cost_window_id: UUID,
    ) -> CostAttributionReport | None:
        row = (
            self._session.execute(
                select(cost_attribution_windows).where(
                    cost_attribution_windows.c.workspace_id == workspace_id,
                    cost_attribution_windows.c.cost_window_id == cost_window_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._report(row) if row is not None else None

    def add_report(self, report: CostAttributionReport) -> None:
        """先写窗口，再写账本和七类聚合；Trigger 保护后续改写。"""

        # 1. 主窗口先冻结发布、价格、对账与总额身份，子事实依赖复合外键绑定同一空间。
        window = report.window
        self._session.execute(
            insert(cost_attribution_windows).values(
                cost_window_id=window.cost_window_id,
                window_identity_digest=window.window_identity_digest,
                workspace_id=window.workspace_id,
                service_id=window.service_id,
                agent_release_id=window.agent_release_id,
                runtime_config_version_id=window.runtime_config_version_id,
                run_configuration_digest=window.run_configuration_digest,
                price_version=window.price_version,
                price_catalog_digest=window.price_catalog_digest,
                currency=window.currency,
                network_region=window.network_region,
                window_started_at=window.window_started_at,
                window_ended_at=window.window_ended_at,
                evidence_kind=window.evidence_kind,
                collector_version=window.collector_version,
                attribution_status=window.attribution_status,
                price_verification_status=window.price_verification_status,
                reconciliation_status=window.reconciliation_status,
                entry_count=window.entry_count,
                failed_entry_count=window.failed_entry_count,
                retry_entry_count=window.retry_entry_count,
                estimated_amount_minor=window.estimated_amount_minor,
                reported_amount_minor=window.reported_amount_minor,
                recognized_amount_minor=window.recognized_amount_minor,
                supplier_statement_amount_minor=window.supplier_statement_amount_minor,
                reconciliation_difference_minor=window.reconciliation_difference_minor,
                supplier_account_digest=window.supplier_account_digest,
                supplier_statement_digest=window.supplier_statement_digest,
                supplier_evidence_digest=window.supplier_evidence_digest,
                reason_codes=list(window.reason_codes),
                result_digest=window.result_digest,
                created_by_actor_id=window.created_by_actor_id,
                completed_at=window.completed_at,
            )
        )
        # 2. 每个来源 Attempt 独立写账，位置列固定采集顺序并保留失败与重试成本。
        if report.entries:
            self._session.execute(
                insert(cost_ledger_entries),
                [
                    {
                        "ledger_entry_id": item.ledger_entry_id,
                        "cost_window_id": item.cost_window_id,
                        "workspace_id": item.workspace_id,
                        "position": position,
                        "service_id": item.service_id,
                        "agent_release_id": item.agent_release_id,
                        "component": item.component,
                        "source_kind": item.source_kind,
                        "source_record_id": item.source_record_id,
                        "meter_key": item.meter_key,
                        "attempt_no": item.attempt_no,
                        "outcome": item.outcome,
                        "is_retry": item.is_retry,
                        "quantity": item.quantity,
                        "usage_unit": item.usage_unit,
                        "unit_size": item.unit_size,
                        "unit_price_minor": item.unit_price_minor,
                        "estimated_amount_minor": item.estimated_amount_minor,
                        "reported_amount_minor": item.reported_amount_minor,
                        "recognized_amount_minor": item.recognized_amount_minor,
                        "amount_source": item.amount_source,
                        "price_version": item.price_version,
                        "currency": item.currency,
                        "evidence_digest": item.evidence_digest,
                    }
                    for position, item in enumerate(report.entries, start=1)
                ],
            )
        # 3. 七类组件聚合始终完整写入，零用量组件也保留稳定位置以支持跨窗口比较。
        self._session.execute(
            insert(cost_attribution_lines),
            [
                {
                    "cost_window_id": item.cost_window_id,
                    "workspace_id": item.workspace_id,
                    "position": position,
                    "component": item.component,
                    "entry_count": item.entry_count,
                    "failed_entry_count": item.failed_entry_count,
                    "retry_entry_count": item.retry_entry_count,
                    "quantity": item.quantity,
                    "estimated_amount_minor": item.estimated_amount_minor,
                    "reported_amount_minor": item.reported_amount_minor,
                    "recognized_amount_minor": item.recognized_amount_minor,
                    "evidence_digest": item.evidence_digest,
                }
                for position, item in enumerate(report.lines, start=1)
            ],
        )

    def _report(self, window_row: RowMapping) -> CostAttributionReport:
        window_id = cast(UUID, window_row["cost_window_id"])
        workspace_id = cast(UUID, window_row["workspace_id"])
        entry_rows = (
            self._session.execute(
                select(cost_ledger_entries)
                .where(
                    cost_ledger_entries.c.workspace_id == workspace_id,
                    cost_ledger_entries.c.cost_window_id == window_id,
                )
                .order_by(cost_ledger_entries.c.position)
            )
            .mappings()
            .all()
        )
        line_rows = (
            self._session.execute(
                select(cost_attribution_lines)
                .where(
                    cost_attribution_lines.c.workspace_id == workspace_id,
                    cost_attribution_lines.c.cost_window_id == window_id,
                )
                .order_by(cost_attribution_lines.c.position)
            )
            .mappings()
            .all()
        )
        return CostAttributionReport(
            _window(window_row),
            tuple(_entry(row) for row in entry_rows),
            tuple(_line(row) for row in line_rows),
        )


class SqlAlchemyCostAttributionUnitOfWork:
    """为成本报告提供显式短事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._costs: SqlAlchemyCostAttributionRepository | None = None

    @property
    def costs(self) -> SqlAlchemyCostAttributionRepository:
        if self._costs is None:
            raise RuntimeError("Cost Attribution Unit of Work 尚未进入事务范围")
        return self._costs

    def __enter__(self) -> SqlAlchemyCostAttributionUnitOfWork:
        if self._session is not None:
            raise RuntimeError("Cost Attribution Unit of Work 不允许嵌套事务")
        self._session = self._session_factory()
        self._costs = SqlAlchemyCostAttributionRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            if exc_type is not None:
                self._session.rollback()
            self._session.close()
        self._costs = None
        self._session = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Cost Attribution Unit of Work 尚未进入事务范围")
        self._session.commit()


def _window(row: RowMapping) -> CostAttributionWindow:
    return CostAttributionWindow(
        cost_window_id=cast(UUID, row["cost_window_id"]),
        window_identity_digest=cast(str, row["window_identity_digest"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        service_id=cast(UUID, row["service_id"]),
        agent_release_id=cast(UUID, row["agent_release_id"]),
        runtime_config_version_id=cast(UUID, row["runtime_config_version_id"]),
        run_configuration_digest=cast(str, row["run_configuration_digest"]),
        price_version=cast(str, row["price_version"]),
        price_catalog_digest=cast(str, row["price_catalog_digest"]),
        currency=cast(str, row["currency"]),
        network_region=cast(str, row["network_region"]),
        window_started_at=cast(datetime, row["window_started_at"]),
        window_ended_at=cast(datetime, row["window_ended_at"]),
        evidence_kind=cast(CostEvidenceKind, row["evidence_kind"]),
        collector_version=cast(str, row["collector_version"]),
        attribution_status=cast(CostVerificationStatus, row["attribution_status"]),
        price_verification_status=cast(CostVerificationStatus, row["price_verification_status"]),
        reconciliation_status=cast(CostReconciliationStatus, row["reconciliation_status"]),
        entry_count=cast(int, row["entry_count"]),
        failed_entry_count=cast(int, row["failed_entry_count"]),
        retry_entry_count=cast(int, row["retry_entry_count"]),
        estimated_amount_minor=cast(int, row["estimated_amount_minor"]),
        reported_amount_minor=cast(int, row["reported_amount_minor"]),
        recognized_amount_minor=cast(int, row["recognized_amount_minor"]),
        supplier_statement_amount_minor=cast(int | None, row["supplier_statement_amount_minor"]),
        reconciliation_difference_minor=cast(int | None, row["reconciliation_difference_minor"]),
        supplier_account_digest=cast(str | None, row["supplier_account_digest"]),
        supplier_statement_digest=cast(str | None, row["supplier_statement_digest"]),
        supplier_evidence_digest=cast(str, row["supplier_evidence_digest"]),
        reason_codes=tuple(cast(list[str], row["reason_codes"])),
        result_digest=cast(str, row["result_digest"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        completed_at=cast(datetime, row["completed_at"]),
    )


def _entry(row: RowMapping) -> CostLedgerEntry:
    return CostLedgerEntry(
        ledger_entry_id=cast(UUID, row["ledger_entry_id"]),
        cost_window_id=cast(UUID, row["cost_window_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        service_id=cast(UUID, row["service_id"]),
        agent_release_id=cast(UUID, row["agent_release_id"]),
        component=cast(CostComponent, row["component"]),
        source_kind=cast(CostSourceKind, row["source_kind"]),
        source_record_id=cast(UUID, row["source_record_id"]),
        meter_key=cast(str, row["meter_key"]),
        attempt_no=cast(int, row["attempt_no"]),
        outcome=cast(CostOutcome, row["outcome"]),
        is_retry=cast(bool, row["is_retry"]),
        quantity=cast(int, row["quantity"]),
        usage_unit=cast(CostUsageUnit, row["usage_unit"]),
        unit_size=cast(int, row["unit_size"]),
        unit_price_minor=cast(int, row["unit_price_minor"]),
        estimated_amount_minor=cast(int, row["estimated_amount_minor"]),
        reported_amount_minor=cast(int | None, row["reported_amount_minor"]),
        recognized_amount_minor=cast(int, row["recognized_amount_minor"]),
        amount_source=cast(CostAmountSource, row["amount_source"]),
        price_version=cast(str, row["price_version"]),
        currency=cast(str, row["currency"]),
        evidence_digest=cast(str, row["evidence_digest"]),
    )


def _line(row: RowMapping) -> CostAttributionLine:
    return CostAttributionLine(
        cost_window_id=cast(UUID, row["cost_window_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        component=cast(CostComponent, row["component"]),
        entry_count=cast(int, row["entry_count"]),
        failed_entry_count=cast(int, row["failed_entry_count"]),
        retry_entry_count=cast(int, row["retry_entry_count"]),
        quantity=cast(int, row["quantity"]),
        estimated_amount_minor=cast(int, row["estimated_amount_minor"]),
        reported_amount_minor=cast(int, row["reported_amount_minor"]),
        recognized_amount_minor=cast(int, row["recognized_amount_minor"]),
        evidence_digest=cast(str, row["evidence_digest"]),
    )
