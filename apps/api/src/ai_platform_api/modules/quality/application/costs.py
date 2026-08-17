"""编排七类成本归因、逐尝试账本、整数金额和供应商账单对账。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.quality.application.cost_policy import (
    COMPONENT_UNITS,
    COST_COLLECTOR_VERSION,
    COST_COMPONENTS,
    COST_REASON_CODES,
    DIFFERENCE_REASON_CODES,
    MAXIMUM_AMOUNT_MINOR,
    MAXIMUM_ENTRY_COUNT,
    MAXIMUM_QUANTITY,
    MAXIMUM_WINDOW_DAYS,
    SOURCE_COMPONENTS,
)
from ai_platform_api.modules.quality.application.errors import (
    QualityConflictError,
    QualityDeniedError,
    QualityNotFoundError,
    QualityValidationError,
)
from ai_platform_api.modules.quality.domain.costs import (
    CostAmountSource,
    CostAttributionLine,
    CostAttributionReport,
    CostAttributionTarget,
    CostAttributionUnitOfWork,
    CostAttributionWindow,
    CostCollectionBatch,
    CostCollectionRequest,
    CostEvidenceKind,
    CostLedgerEntry,
    CostReconciliationStatus,
    CostReleaseContext,
    CostSupplierStatement,
    CostUsageCollector,
    CostUsageObservation,
    CostVerificationStatus,
)

COST_ATTRIBUTION_NAMESPACE = UUID("55000000-0000-4000-8000-000000000515")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REGION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,63}$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
METER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
RECORD_PERMISSION = "agent.test.execute"
READ_PERMISSIONS = frozenset({"agent.test.read", "operations.records.read"})


class CostAttributionService:
    """从受信采集器生成不可变账本，并保证窗口身份幂等。"""

    def __init__(
        self,
        unit_of_work: CostAttributionUnitOfWork,
        collector: CostUsageCollector,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._collector = collector

    def record(
        self,
        context: RequestContext,
        target: CostAttributionTarget,
    ) -> CostAttributionReport:
        """采集可信用量、解析发布身份并在一个事务写入完整报告。"""

        # 1. 调用方只能选择冻结目标，用量、单价和账单必须来自受信内部采集器。
        _require_record_authorization(context)
        _validate_target(target)
        request = CostCollectionRequest(context.workspace_id, target)
        batch = self._collector.collect(request)
        completed_at = datetime.now(UTC)
        # 2. 发布身份解析、幂等锁和三层事实写入必须共享同一短事务。
        with self._unit_of_work as unit_of_work:
            release = unit_of_work.costs.get_release_context(
                context.workspace_id,
                target.service_id,
                target.agent_release_id,
            )
            if release is None:
                raise QualityNotFoundError
            report = build_cost_attribution_report(
                workspace_id=context.workspace_id,
                target=target,
                batch=batch,
                collector_version=self._collector.collector_version,
                release=release,
                actor_id=context.actor_id,
                completed_at=completed_at,
            )
            unit_of_work.costs.lock_window_identity(report.window.window_identity_digest)
            existing = unit_of_work.costs.get_report_by_identity(
                context.workspace_id,
                report.window.window_identity_digest,
            )
            if existing is not None:
                if existing.window.result_digest != report.window.result_digest:
                    raise QualityConflictError
                return existing
            unit_of_work.costs.add_report(report)
            unit_of_work.commit()
            return report

    def get_report(
        self,
        context: RequestContext,
        cost_window_id: UUID,
    ) -> CostAttributionReport | None:
        """按工作空间读取成本报告，跨空间标识保持不可见。"""

        _require_read_authorization(context)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.costs.get_report(context.workspace_id, cost_window_id)


def build_cost_attribution_report(
    *,
    workspace_id: UUID,
    target: CostAttributionTarget,
    batch: CostCollectionBatch,
    collector_version: str,
    release: CostReleaseContext,
    actor_id: UUID,
    completed_at: datetime,
) -> CostAttributionReport:
    """纯函数构建成本报告；全合成价格永不提升真实价格验证状态。"""

    # 1. 请求目标形成稳定窗口身份；采集器或发布身份漂移只影响结果而不改写身份。
    _validate_target(target)
    _validate_collector_version(collector_version)
    _validate_completed_at(completed_at)
    observations = _validated_observations(batch)
    _validate_supplier_statement(batch.supplier_statement, batch.evidence_kind)
    identity_document = _identity_document(
        workspace_id,
        target,
        collector_version,
        release,
    )
    identity_digest = _digest_document(identity_document)
    cost_window_id = uuid5(COST_ATTRIBUTION_NAMESPACE, identity_digest)
    identity_reasons = _identity_reasons(workspace_id, target, batch, release)

    # 2. 每个 Attempt 独立计价，失败与重试不会从账本或聚合中被过滤。
    entries = tuple(
        _ledger_entry(
            cost_window_id,
            workspace_id,
            target,
            observation,
            batch.evidence_kind,
        )
        for observation in observations
    )
    lines = tuple(
        _attribution_line(cost_window_id, workspace_id, component, entries)
        for component in COST_COMPONENTS
    )
    totals = _totals(entries)
    attribution_status: CostVerificationStatus = (
        "failed" if identity_reasons else "passed" if entries else "not_run"
    )
    if not entries:
        identity_reasons.add("usage_not_run")
    price_status, price_reasons = _price_status(
        batch.evidence_kind,
        observations,
        identity_reasons,
    )
    reconciliation = _reconcile(
        batch.evidence_kind,
        target,
        batch.supplier_statement,
        totals["recognized"],
    )
    reasons = identity_reasons | set(price_reasons) | set(reconciliation[5])
    if not reasons.issubset(COST_REASON_CODES):
        raise QualityValidationError

    # 3. 结果摘要覆盖逐条账本、固定七类聚合和账单差异，但不包含证据正文。
    result_document = {
        **identity_document,
        "evidence_kind": batch.evidence_kind,
        "attribution_status": attribution_status,
        "price_verification_status": price_status,
        "reconciliation_status": reconciliation[0],
        "totals": totals,
        "supplier_statement_amount_minor": reconciliation[1],
        "reconciliation_difference_minor": reconciliation[2],
        "supplier_account_digest": reconciliation[3],
        "supplier_statement_digest": reconciliation[4],
        "reason_codes": sorted(reasons),
        "entries": [_entry_document(item) for item in entries],
        "lines": [_line_document(item) for item in lines],
        "supplier_evidence_digest": reconciliation[6],
    }
    result_digest = _digest_document(result_document)
    window = CostAttributionWindow(
        cost_window_id=cost_window_id,
        window_identity_digest=identity_digest,
        workspace_id=workspace_id,
        service_id=target.service_id,
        agent_release_id=target.agent_release_id,
        runtime_config_version_id=release.runtime_config_version_id,
        run_configuration_digest=target.run_configuration_digest,
        price_version=target.price_version,
        price_catalog_digest=target.price_catalog_digest,
        currency=target.currency,
        network_region=target.network_region,
        window_started_at=target.window_started_at,
        window_ended_at=target.window_ended_at,
        evidence_kind=batch.evidence_kind,
        collector_version=collector_version,
        attribution_status=attribution_status,
        price_verification_status=price_status,
        reconciliation_status=reconciliation[0],
        entry_count=len(entries),
        failed_entry_count=totals["failed_count"],
        retry_entry_count=totals["retry_count"],
        estimated_amount_minor=totals["estimated"],
        reported_amount_minor=totals["reported"],
        recognized_amount_minor=totals["recognized"],
        supplier_statement_amount_minor=reconciliation[1],
        reconciliation_difference_minor=reconciliation[2],
        supplier_account_digest=reconciliation[3],
        supplier_statement_digest=reconciliation[4],
        supplier_evidence_digest=reconciliation[6],
        reason_codes=tuple(sorted(reasons)),
        result_digest=result_digest,
        created_by_actor_id=actor_id,
        completed_at=completed_at,
    )
    return CostAttributionReport(window, entries, lines)


def _ledger_entry(
    cost_window_id: UUID,
    workspace_id: UUID,
    target: CostAttributionTarget,
    observation: CostUsageObservation,
    evidence_kind: CostEvidenceKind,
) -> CostLedgerEntry:
    if evidence_kind == "authorized_real" and observation.estimate_source == "synthetic_rate":
        # 真实证据窗口保留失败结论，但不会通过合成价目制造供应商报告金额。
        reported = None
    else:
        reported = observation.reported_amount_minor
    estimated = _rounded_amount(
        observation.quantity,
        observation.unit_price_minor,
        observation.unit_size,
    )
    recognized = reported if reported is not None else estimated
    amount_source: CostAmountSource = (
        "provider_reported" if reported is not None else observation.estimate_source
    )
    entry_identity = ":".join(
        (
            str(cost_window_id),
            observation.source_kind,
            str(observation.source_record_id),
            observation.meter_key,
        )
    )
    return CostLedgerEntry(
        ledger_entry_id=uuid5(COST_ATTRIBUTION_NAMESPACE, entry_identity),
        cost_window_id=cost_window_id,
        workspace_id=workspace_id,
        service_id=target.service_id,
        agent_release_id=target.agent_release_id,
        component=observation.component,
        source_kind=observation.source_kind,
        source_record_id=observation.source_record_id,
        meter_key=observation.meter_key,
        attempt_no=observation.attempt_no,
        outcome=observation.outcome,
        is_retry=observation.is_retry,
        quantity=observation.quantity,
        usage_unit=observation.usage_unit,
        unit_size=observation.unit_size,
        unit_price_minor=observation.unit_price_minor,
        estimated_amount_minor=estimated,
        reported_amount_minor=reported,
        recognized_amount_minor=recognized,
        amount_source=amount_source,
        price_version=observation.price_version,
        currency=observation.currency,
        evidence_digest=_digest_document(observation.evidence),
    )


def _attribution_line(
    cost_window_id: UUID,
    workspace_id: UUID,
    component: str,
    entries: tuple[CostLedgerEntry, ...],
) -> CostAttributionLine:
    component_value = next(item for item in COST_COMPONENTS if item == component)
    selected = tuple(item for item in entries if item.component == component_value)
    return CostAttributionLine(
        cost_window_id=cost_window_id,
        workspace_id=workspace_id,
        component=component_value,
        entry_count=len(selected),
        failed_entry_count=sum(item.outcome != "succeeded" for item in selected),
        retry_entry_count=sum(item.is_retry for item in selected),
        quantity=sum(item.quantity for item in selected),
        estimated_amount_minor=sum(item.estimated_amount_minor for item in selected),
        reported_amount_minor=sum(item.reported_amount_minor or 0 for item in selected),
        recognized_amount_minor=sum(item.recognized_amount_minor for item in selected),
        evidence_digest=_digest_document([_entry_document(item) for item in selected]),
    )


def _price_status(
    evidence_kind: CostEvidenceKind,
    observations: tuple[CostUsageObservation, ...],
    identity_reasons: set[str],
) -> tuple[CostVerificationStatus, tuple[str, ...]]:
    """真实价格必须来自受审核证据；合成单价只能验证计算机制。"""

    if evidence_kind != "authorized_real":
        return "not_configured", ("real_price_not_configured",)
    if identity_reasons & {
        "collector_identity_drift",
        "release_identity_drift",
        "price_version_mismatch",
        "currency_mismatch",
    }:
        return "failed", ()
    if any(item.estimate_source == "synthetic_rate" for item in observations):
        return "failed", ("real_price_not_verified",)
    return ("passed", ()) if observations else ("not_run", ())


def _reconcile(
    evidence_kind: CostEvidenceKind,
    target: CostAttributionTarget,
    statement: CostSupplierStatement,
    ledger_amount_minor: int,
) -> tuple[
    CostReconciliationStatus,
    int | None,
    int | None,
    str | None,
    str | None,
    tuple[str, ...],
    str,
]:
    # 1. 非真实证据和未提供账单必须保留显式状态，不能被零差异误判为已对账。
    evidence_digest = _digest_document(statement.evidence)
    if evidence_kind != "authorized_real":
        return (
            "not_configured",
            None,
            None,
            None,
            None,
            ("real_price_not_configured",),
            evidence_digest,
        )
    if statement.status == "not_configured":
        return (
            "not_configured",
            None,
            None,
            None,
            None,
            ("supplier_statement_not_configured",),
            evidence_digest,
        )
    if statement.status == "not_run":
        return (
            "not_run",
            None,
            None,
            statement.supplier_account_digest,
            None,
            ("supplier_statement_not_run",),
            evidence_digest,
        )
    # 2. 已提供账单必须具备金额且币种与冻结窗口一致，身份不一致时直接失败关闭。
    amount = statement.amount_minor
    if amount is None:
        raise QualityValidationError
    if statement.currency != target.currency:
        return (
            "failed",
            amount,
            None,
            statement.supplier_account_digest,
            statement.statement_digest,
            ("supplier_statement_currency_mismatch",),
            evidence_digest,
        )
    # 3. 金额完全一致才算匹配；非零差异只有携带白名单原因时才可解释。
    difference = amount - ledger_amount_minor
    if difference == 0:
        return (
            "matched",
            amount,
            0,
            statement.supplier_account_digest,
            statement.statement_digest,
            (),
            evidence_digest,
        )
    if statement.difference_reason_codes:
        return (
            "explained",
            amount,
            difference,
            statement.supplier_account_digest,
            statement.statement_digest,
            statement.difference_reason_codes,
            evidence_digest,
        )
    return (
        "failed",
        amount,
        difference,
        statement.supplier_account_digest,
        statement.statement_digest,
        ("supplier_statement_unexplained_difference",),
        evidence_digest,
    )


def _validated_observations(batch: CostCollectionBatch) -> tuple[CostUsageObservation, ...]:
    observations = batch.observations
    if len(observations) > MAXIMUM_ENTRY_COUNT:
        raise QualityValidationError
    seen: set[tuple[str, UUID, str]] = set()
    for item in observations:
        _validate_observation(item, batch.evidence_kind, batch.target)
        identity = (item.source_kind, item.source_record_id, item.meter_key)
        if identity in seen:
            raise QualityValidationError
        seen.add(identity)
    return tuple(
        sorted(
            observations,
            key=lambda item: (
                COST_COMPONENTS.index(item.component),
                item.source_kind,
                item.source_record_id.hex,
                item.meter_key,
            ),
        )
    )


def _validate_observation(
    item: CostUsageObservation,
    evidence_kind: CostEvidenceKind,
    batch_target: CostAttributionTarget,
) -> None:
    integers = (
        item.attempt_no,
        item.quantity,
        item.unit_size,
        item.unit_price_minor,
    )
    reported_valid = item.reported_amount_minor is None or _is_bounded_integer(
        item.reported_amount_minor,
        MAXIMUM_AMOUNT_MINOR,
    )
    if (
        SOURCE_COMPONENTS.get(item.source_kind) != item.component
        or item.usage_unit not in COMPONENT_UNITS[item.component]
        or METER_PATTERN.fullmatch(item.meter_key) is None
        or not all(isinstance(value, int) and not isinstance(value, bool) for value in integers)
        or item.attempt_no < 1
        or item.is_retry != (item.attempt_no > 1)
        or not 0 <= item.quantity <= MAXIMUM_QUANTITY
        or not 1 <= item.unit_size <= MAXIMUM_QUANTITY
        or not 0 <= item.unit_price_minor <= MAXIMUM_AMOUNT_MINOR
        or not reported_valid
        or item.price_version != batch_target.price_version
        or item.currency != batch_target.currency
        or (evidence_kind == "synthetic" and item.estimate_source != "synthetic_rate")
        or (evidence_kind == "synthetic" and item.reported_amount_minor is not None)
    ):
        raise QualityValidationError
    _digest_document(item.evidence)


def _validate_supplier_statement(
    statement: CostSupplierStatement,
    evidence_kind: CostEvidenceKind,
) -> None:
    if not set(statement.difference_reason_codes).issubset(DIFFERENCE_REASON_CODES):
        raise QualityValidationError
    if len(set(statement.difference_reason_codes)) != len(statement.difference_reason_codes):
        raise QualityValidationError
    provided_fields = (
        statement.supplier_account_digest,
        statement.statement_digest,
        statement.amount_minor,
        statement.currency,
    )
    if statement.status == "provided":
        if (
            evidence_kind != "authorized_real"
            or any(value is None for value in provided_fields)
            or DIGEST_PATTERN.fullmatch(statement.supplier_account_digest or "") is None
            or DIGEST_PATTERN.fullmatch(statement.statement_digest or "") is None
            or not _is_bounded_integer(statement.amount_minor, MAXIMUM_AMOUNT_MINOR)
            or CURRENCY_PATTERN.fullmatch(statement.currency or "") is None
        ):
            raise QualityValidationError
    elif statement.status == "not_run":
        if statement.amount_minor is not None or statement.statement_digest is not None:
            raise QualityValidationError
    elif any(value is not None for value in provided_fields) or statement.difference_reason_codes:
        raise QualityValidationError
    _digest_document(statement.evidence)


def _identity_reasons(
    workspace_id: UUID,
    target: CostAttributionTarget,
    batch: CostCollectionBatch,
    release: CostReleaseContext,
) -> set[str]:
    reasons: set[str] = set()
    if batch.workspace_id != workspace_id or batch.target != target:
        reasons.add("collector_identity_drift")
    if (
        release.service_id != target.service_id
        or release.agent_release_id != target.agent_release_id
        or release.run_configuration_digest != target.run_configuration_digest
    ):
        reasons.add("release_identity_drift")
    if batch.target.price_version != target.price_version:
        reasons.add("price_version_mismatch")
    if batch.target.currency != target.currency:
        reasons.add("currency_mismatch")
    return reasons


def _identity_document(
    workspace_id: UUID,
    target: CostAttributionTarget,
    collector_version: str,
    release: CostReleaseContext,
) -> dict[str, object]:
    return {
        "workspace_id": str(workspace_id),
        "service_id": str(target.service_id),
        "agent_release_id": str(target.agent_release_id),
        "runtime_config_version_id": str(release.runtime_config_version_id),
        "run_configuration_digest": target.run_configuration_digest,
        "price_version": target.price_version,
        "price_catalog_digest": target.price_catalog_digest,
        "currency": target.currency,
        "network_region": target.network_region,
        "window_started_at": target.window_started_at.isoformat(),
        "window_ended_at": target.window_ended_at.isoformat(),
        "collector_version": collector_version,
    }


def _entry_document(item: CostLedgerEntry) -> dict[str, object]:
    return {
        "ledger_entry_id": str(item.ledger_entry_id),
        "component": item.component,
        "source_kind": item.source_kind,
        "source_record_id": str(item.source_record_id),
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


def _line_document(item: CostAttributionLine) -> dict[str, object]:
    return {
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


def _totals(entries: tuple[CostLedgerEntry, ...]) -> dict[str, int]:
    return {
        "entry_count": len(entries),
        "failed_count": sum(item.outcome != "succeeded" for item in entries),
        "retry_count": sum(item.is_retry for item in entries),
        "estimated": sum(item.estimated_amount_minor for item in entries),
        "reported": sum(item.reported_amount_minor or 0 for item in entries),
        "recognized": sum(item.recognized_amount_minor for item in entries),
    }


def _rounded_amount(quantity: int, unit_price_minor: int, unit_size: int) -> int:
    """按单条账本向上取整，避免亚最小货币单位被静默丢弃。"""

    amount = (quantity * unit_price_minor + unit_size - 1) // unit_size
    if amount > MAXIMUM_AMOUNT_MINOR:
        raise QualityValidationError
    return amount


def _validate_target(target: CostAttributionTarget) -> None:
    if (
        target.window_started_at.tzinfo is None
        or target.window_ended_at.tzinfo is None
        or target.window_ended_at <= target.window_started_at
        or target.window_ended_at - target.window_started_at > timedelta(days=MAXIMUM_WINDOW_DAYS)
        or DIGEST_PATTERN.fullmatch(target.run_configuration_digest) is None
        or DIGEST_PATTERN.fullmatch(target.price_catalog_digest) is None
        or VERSION_PATTERN.fullmatch(target.price_version) is None
        or CURRENCY_PATTERN.fullmatch(target.currency) is None
        or REGION_PATTERN.fullmatch(target.network_region) is None
    ):
        raise QualityValidationError


def _validate_collector_version(version: str) -> None:
    if version != COST_COLLECTOR_VERSION:
        raise QualityValidationError


def _validate_completed_at(completed_at: datetime) -> None:
    if completed_at.tzinfo is None:
        raise QualityValidationError


def _is_bounded_integer(value: object, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _digest_document(document: object) -> str:
    try:
        payload = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise QualityValidationError from error
    return hashlib.sha256(payload).hexdigest()


def _require_record_authorization(context: RequestContext) -> None:
    if (
        context.audit_authorization is None
        or context.authorized_permission_code != RECORD_PERMISSION
        or not context.authorized_workspace
    ):
        raise QualityDeniedError


def _require_read_authorization(context: RequestContext) -> None:
    if (
        context.audit_authorization is None
        or context.authorized_permission_code not in READ_PERMISSIONS
        or not context.authorized_workspace
    ):
        raise QualityDeniedError
