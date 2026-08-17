"""编排质量运营窗口、真实证据门禁和四维验证状态。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.quality.application.errors import (
    QualityConflictError,
    QualityDeniedError,
    QualityNotFoundError,
    QualityValidationError,
)
from ai_platform_api.modules.quality.application.operations_policy import (
    HARD_FAILURE_REASON_CODES,
    MINIMUM_ANSWER_ACCEPTANCE_RATE_BPS,
    MINIMUM_CITATION_PRESENCE_RATE_BPS,
    MINIMUM_CITATION_SUPPORT_RATE_BPS,
    MINIMUM_POSITIVE_FEEDBACK_RATE_BPS,
    QUALITY_OPERATION_COLLECTOR_VERSION,
    QUALITY_OPERATION_REASON_CODES,
    QUALITY_OPERATION_SOURCES,
    REQUIRED_SOURCES,
    SOURCE_POLICIES,
    QualitySourcePolicy,
)
from ai_platform_api.modules.quality.domain.operations import (
    QualityEvaluationOperationContext,
    QualityEvidenceKind,
    QualityOperationBatch,
    QualityOperationCollectionRequest,
    QualityOperationCollector,
    QualityOperationReport,
    QualityOperationSourceResult,
    QualityOperationTarget,
    QualityOperationUnitOfWork,
    QualityOperationWindow,
    QualityProviderEvidence,
    QualityProviderSnapshot,
    QualityReleaseGateStatus,
    QualitySourceObservation,
    QualitySourceStatus,
    VerificationStatus,
)

QUALITY_OPERATION_NAMESPACE = UUID("55000000-0000-4000-8000-000000000514")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REGION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,63}$")
RECORD_PERMISSION = "agent.test.execute"
READ_PERMISSIONS = frozenset({"agent.test.read", "operations.records.read"})
MAXIMUM_WINDOW = timedelta(days=31)


class QualityOperationService:
    """汇聚固定来源，并保证窗口身份幂等、结果不可漂移。"""

    def __init__(
        self,
        unit_of_work: QualityOperationUnitOfWork,
        collector: QualityOperationCollector,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._collector = collector

    def record(
        self,
        context: RequestContext,
        target: QualityOperationTarget,
    ) -> QualityOperationReport:
        """采集可信聚合、解析权威供应商身份并原子保存窗口报告。"""

        # 1. 浏览器只选择冻结目标，实际聚合和证据类型必须来自受信内部采集器。
        _require_record_authorization(context)
        _validate_target(target)
        collection_request = QualityOperationCollectionRequest(context.workspace_id, target)
        batch = self._collector.collect(collection_request)
        completed_at = datetime.now(UTC)
        # 2. 权威发布身份、供应商路由、幂等锁和报告写入必须处于同一短事务。
        with self._unit_of_work as unit_of_work:
            evaluation = unit_of_work.operations.get_evaluation_context(
                context.workspace_id,
                target.evaluation_run_id,
            )
            if evaluation is None:
                raise QualityNotFoundError
            provider = (
                unit_of_work.operations.get_provider_snapshot(evaluation, batch.provider)
                if batch.provider is not None
                else None
            )
            report = build_quality_operation_report(
                workspace_id=context.workspace_id,
                target=target,
                batch=batch,
                collector_version=self._collector.collector_version,
                evaluation=evaluation,
                provider=provider,
                actor_id=context.actor_id,
                completed_at=completed_at,
            )
            unit_of_work.operations.lock_window_identity(report.window.window_identity_digest)
            existing = unit_of_work.operations.get_report_by_identity(
                context.workspace_id,
                report.window.window_identity_digest,
            )
            if existing is not None:
                if existing.window.result_digest != report.window.result_digest:
                    raise QualityConflictError
                return existing
            unit_of_work.operations.add_report(report)
            unit_of_work.commit()
            return report

    def get_report(
        self,
        context: RequestContext,
        quality_window_id: UUID,
    ) -> QualityOperationReport | None:
        """按工作空间读取运营报告，跨空间标识统一返回不可见。"""

        _require_read_authorization(context)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.operations.get_report(
                context.workspace_id,
                quality_window_id,
            )


def build_quality_operation_report(
    *,
    workspace_id: UUID,
    target: QualityOperationTarget,
    batch: QualityOperationBatch,
    collector_version: str,
    evaluation: QualityEvaluationOperationContext,
    provider: QualityProviderSnapshot | None,
    actor_id: UUID,
    completed_at: datetime,
) -> QualityOperationReport:
    """纯函数构建固定三来源报告；真实结论必须同时满足供应商与样本门禁。"""

    # 1. 先验证调用边界并冻结完整身份，采集器漂移会传播到固定三来源。
    _validate_target(target)
    _validate_collector_version(collector_version)
    _validate_completed_at(completed_at)
    _validate_provider_evidence(batch.provider)
    identity_drift = _batch_identity_drift(workspace_id, target, batch)
    observations = _validated_observations(batch.observations)
    identity_document = _identity_document(
        workspace_id,
        target,
        batch,
        collector_version,
        evaluation,
    )
    window_identity_digest = _digest_document(identity_document)
    quality_window_id = uuid5(QUALITY_OPERATION_NAMESPACE, window_identity_digest)

    # 2. 三来源独立判定样本、比例和硬失败，再结合供应商状态计算四维结论。
    source_results = tuple(
        _source_result(
            quality_window_id,
            workspace_id,
            policy,
            observations.get(policy.source),
            identity_drift=identity_drift,
        )
        for policy in SOURCE_POLICIES
    )
    provider_status, provider_reasons = _provider_status(
        batch.evidence_kind,
        batch.provider,
        provider,
    )
    core_status: VerificationStatus = "failed" if identity_drift else "passed"
    ai_status, ai_reasons = _ai_quality_status(
        target,
        batch.evidence_kind,
        evaluation,
        provider_status,
        source_results,
        identity_drift=identity_drift,
    )
    gate_status: QualityReleaseGateStatus = (
        "passed" if ai_status == "passed" else "failed" if ai_status == "failed" else "blocked"
    )
    reasons = set(provider_reasons) | set(ai_reasons)
    required_sources = REQUIRED_SOURCES[target.gate_stage]
    reasons.update(
        reason
        for result in source_results
        if result.source in required_sources
        for reason in result.reason_codes
    )
    if not reasons.issubset(QUALITY_OPERATION_REASON_CODES):
        raise QualityValidationError

    # 3. 最终摘要覆盖全部来源，但窗口原因只包含当前发布阶段真正要求的来源。
    result_document = {
        **identity_document,
        "core_functional_status": core_status,
        "provider_integration_status": provider_status,
        "ai_quality_status": ai_status,
        "capacity_certification_status": "not_run",
        "release_gate_status": gate_status,
        "reason_codes": sorted(reasons),
        "sources": [_source_document(item) for item in source_results],
    }
    result_digest = _digest_document(result_document)
    provider_evidence = batch.provider
    window = QualityOperationWindow(
        quality_window_id=quality_window_id,
        window_identity_digest=window_identity_digest,
        workspace_id=workspace_id,
        evaluation_run_id=evaluation.evaluation_run_id,
        evaluation_result_digest=evaluation.evaluation_result_digest,
        dataset_version_id=evaluation.dataset_version_id,
        dataset_digest=evaluation.dataset_digest,
        service_id=evaluation.service_id,
        agent_release_id=evaluation.agent_release_id,
        run_configuration_digest=evaluation.run_configuration_digest,
        gate_stage=target.gate_stage,
        evidence_kind=batch.evidence_kind,
        collector_version=collector_version,
        provider_id=provider_evidence.provider_id if provider_evidence is not None else None,
        provider_configuration_version=(
            provider_evidence.provider_configuration_version
            if provider_evidence is not None
            else None
        ),
        model_id=provider_evidence.model_id if provider_evidence is not None else None,
        model_parameters_digest=target.model_parameters_digest,
        network_region=target.network_region,
        window_started_at=target.window_started_at,
        window_ended_at=target.window_ended_at,
        core_functional_status=core_status,
        provider_integration_status=provider_status,
        ai_quality_status=ai_status,
        capacity_certification_status="not_run",
        release_gate_status=gate_status,
        reason_codes=tuple(sorted(reasons)),
        result_digest=result_digest,
        created_by_actor_id=actor_id,
        completed_at=completed_at,
    )
    return QualityOperationReport(window, source_results)


def _source_result(
    window_id: UUID,
    workspace_id: UUID,
    policy: QualitySourcePolicy,
    observation: QualitySourceObservation | None,
    *,
    identity_drift: bool,
) -> QualityOperationSourceResult:
    """按来源独立计算比例；样本不足保持未执行，硬失败直接失败。"""

    # 1. 固定来源即使缺失也生成显式 not_run，身份漂移则提升为失败。
    if observation is None:
        return _empty_source_result(
            window_id,
            workspace_id,
            policy.source,
            identity_drift=identity_drift,
        )
    # 2. 所有比例使用整数基点，质量阈值和安全零容忍分别生成稳定原因码。
    citation_presence = _rate(observation.valid_citation_count, observation.citation_claim_count)
    citation_support = _rate(
        observation.supported_citation_count,
        observation.valid_citation_count,
    )
    answer_acceptance = _rate(
        observation.acceptable_answer_count,
        observation.answer_evaluated_count,
    )
    positive_feedback = _rate(
        observation.positive_feedback_count,
        observation.feedback_count,
    )
    reasons: set[str] = set()
    if identity_drift:
        reasons.add("collector_identity_drift")
    if observation.sample_count < policy.minimum_sample_count:
        reasons.add("source_sample_insufficient")
    _citation_reasons(policy, citation_presence, citation_support, reasons)
    _answer_reasons(policy, answer_acceptance, reasons)
    _feedback_reasons(policy, positive_feedback, reasons)
    if observation.unauthorized_access_count:
        reasons.add("unauthorized_access")
    if observation.restricted_field_leakage_count:
        reasons.add("restricted_field_leakage")
    if observation.unauthorized_tool_call_count:
        reasons.add("unauthorized_tool_call")
    status = _source_status(reasons)
    return QualityOperationSourceResult(
        window_id,
        workspace_id,
        policy.source,
        status,
        observation.sample_count,
        observation.citation_claim_count,
        observation.valid_citation_count,
        observation.supported_citation_count,
        citation_presence,
        citation_support,
        observation.answer_evaluated_count,
        observation.acceptable_answer_count,
        answer_acceptance,
        observation.feedback_count,
        observation.positive_feedback_count,
        positive_feedback,
        observation.tool_call_count,
        observation.unauthorized_access_count,
        observation.restricted_field_leakage_count,
        observation.unauthorized_tool_call_count,
        tuple(sorted(reasons)),
        _digest_document(observation.evidence),
    )


def _empty_source_result(
    window_id: UUID,
    workspace_id: UUID,
    source: str,
    *,
    identity_drift: bool,
) -> QualityOperationSourceResult:
    """缺少来源必须显式保存 not_run，不能用全零指标伪装已测。"""

    source_value = next(item for item in QUALITY_OPERATION_SOURCES if item == source)
    reasons = (
        ("collector_identity_drift", "source_not_run") if identity_drift else ("source_not_run",)
    )
    return QualityOperationSourceResult(
        window_id,
        workspace_id,
        source_value,
        "failed" if identity_drift else "not_run",
        0,
        0,
        0,
        0,
        None,
        None,
        0,
        0,
        None,
        0,
        0,
        None,
        0,
        0,
        0,
        0,
        reasons,
        _digest_document({}),
    )


def _provider_status(
    evidence_kind: QualityEvidenceKind,
    evidence: QualityProviderEvidence | None,
    provider: QualityProviderSnapshot | None,
) -> tuple[VerificationStatus, tuple[str, ...]]:
    """Mock 或合成证据永不提升真实供应商联调状态。"""

    if evidence_kind != "authorized_real":
        return "not_configured", ("real_evidence_not_configured",)
    if evidence is None or provider is None:
        return "not_configured", ("provider_not_configured",)
    if (
        not provider.route_matches
        or provider.configuration_version != evidence.provider_configuration_version
    ):
        return "failed", ("provider_route_mismatch",)
    if provider.policy_review_status == "pending":
        return "not_run", ("provider_review_pending",)
    if provider.policy_review_status != "approved":
        return "failed", ("provider_review_rejected",)
    if provider.probe_status == "not_run":
        return "not_run", ("provider_probe_not_run",)
    if provider.probe_status != "passed":
        return "failed", ("provider_probe_failed",)
    if provider.status != "active":
        return "failed", ("provider_inactive",)
    return "passed", ()


def _ai_quality_status(
    target: QualityOperationTarget,
    evidence_kind: QualityEvidenceKind,
    evaluation: QualityEvaluationOperationContext,
    provider_status: VerificationStatus,
    sources: tuple[QualityOperationSourceResult, ...],
    *,
    identity_drift: bool,
) -> tuple[VerificationStatus, tuple[str, ...]]:
    if evidence_kind != "authorized_real" or provider_status == "not_configured":
        return "not_configured", ("real_evidence_not_configured",)
    if provider_status == "not_run":
        return "not_run", ()
    if provider_status == "failed" or identity_drift or evaluation.evaluation_status == "failed":
        reasons = ("evaluation_failed",) if evaluation.evaluation_status == "failed" else ()
        return "failed", reasons
    required = REQUIRED_SOURCES[target.gate_stage]
    required_results = tuple(item for item in sources if item.source in required)
    if any(item.status == "failed" for item in required_results):
        return "failed", ()
    if any(item.status == "not_run" for item in required_results):
        return "not_run", ()
    return "passed", ()


def _citation_reasons(
    policy: QualitySourcePolicy,
    presence: int | None,
    support: int | None,
    reasons: set[str],
) -> None:
    if not policy.require_citations:
        return
    if presence is None or support is None:
        reasons.add("citation_evidence_missing")
        return
    if presence < MINIMUM_CITATION_PRESENCE_RATE_BPS:
        reasons.add("citation_presence_below_threshold")
    if support < MINIMUM_CITATION_SUPPORT_RATE_BPS:
        reasons.add("citation_support_below_threshold")


def _answer_reasons(
    policy: QualitySourcePolicy,
    acceptance: int | None,
    reasons: set[str],
) -> None:
    if not policy.require_answers:
        return
    if acceptance is None:
        reasons.add("answer_evidence_missing")
    elif acceptance < MINIMUM_ANSWER_ACCEPTANCE_RATE_BPS:
        reasons.add("answer_acceptance_below_threshold")


def _feedback_reasons(
    policy: QualitySourcePolicy,
    positive: int | None,
    reasons: set[str],
) -> None:
    if not policy.require_feedback:
        return
    if positive is None:
        reasons.add("feedback_evidence_missing")
    elif positive < MINIMUM_POSITIVE_FEEDBACK_RATE_BPS:
        reasons.add("positive_feedback_below_threshold")


def _source_status(reasons: set[str]) -> QualitySourceStatus:
    not_run_reasons = {
        "source_sample_insufficient",
        "citation_evidence_missing",
        "answer_evidence_missing",
        "feedback_evidence_missing",
    }
    if reasons & HARD_FAILURE_REASON_CODES or "collector_identity_drift" in reasons:
        return "failed"
    if reasons - not_run_reasons:
        return "failed"
    if reasons:
        return "not_run"
    return "passed"


def _validated_observations(
    observations: tuple[QualitySourceObservation, ...],
) -> dict[str, QualitySourceObservation]:
    if len(observations) > len(QUALITY_OPERATION_SOURCES):
        raise QualityValidationError
    result: dict[str, QualitySourceObservation] = {}
    for item in observations:
        if item.source in result:
            raise QualityValidationError
        _validate_observation(item)
        result[item.source] = item
    return result


def _validate_observation(observation: QualitySourceObservation) -> None:
    counts = (
        observation.sample_count,
        observation.citation_claim_count,
        observation.valid_citation_count,
        observation.supported_citation_count,
        observation.answer_evaluated_count,
        observation.acceptable_answer_count,
        observation.feedback_count,
        observation.positive_feedback_count,
        observation.tool_call_count,
        observation.unauthorized_access_count,
        observation.restricted_field_leakage_count,
        observation.unauthorized_tool_call_count,
    )
    valid_counts = all(
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1_000_000
        for value in counts
    )
    ordered_counts = (
        observation.valid_citation_count <= observation.citation_claim_count
        and observation.supported_citation_count <= observation.valid_citation_count
        and observation.acceptable_answer_count <= observation.answer_evaluated_count
        and observation.positive_feedback_count <= observation.feedback_count
        and observation.unauthorized_tool_call_count <= observation.tool_call_count
    )
    if not valid_counts or not ordered_counts:
        raise QualityValidationError
    _digest_document(observation.evidence)


def _batch_identity_drift(
    workspace_id: UUID,
    target: QualityOperationTarget,
    batch: QualityOperationBatch,
) -> bool:
    return batch.workspace_id != workspace_id or batch.target != target


def _validate_target(target: QualityOperationTarget) -> None:
    if (
        target.window_started_at.tzinfo is None
        or target.window_ended_at.tzinfo is None
        or target.window_ended_at <= target.window_started_at
        or target.window_ended_at - target.window_started_at > MAXIMUM_WINDOW
        or DIGEST_PATTERN.fullmatch(target.model_parameters_digest) is None
        or REGION_PATTERN.fullmatch(target.network_region) is None
    ):
        raise QualityValidationError


def _validate_collector_version(collector_version: str) -> None:
    if collector_version != QUALITY_OPERATION_COLLECTOR_VERSION:
        raise QualityValidationError


def _validate_completed_at(completed_at: datetime) -> None:
    if completed_at.tzinfo is None:
        raise QualityValidationError


def _validate_provider_evidence(evidence: QualityProviderEvidence | None) -> None:
    if evidence is None:
        return
    if (
        not isinstance(evidence.provider_configuration_version, int)
        or isinstance(evidence.provider_configuration_version, bool)
        or evidence.provider_configuration_version < 1
        or not 1 <= len(evidence.model_id.strip()) <= 255
    ):
        raise QualityValidationError


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


def _rate(numerator: int, denominator: int) -> int | None:
    return round(numerator * 10_000 / denominator) if denominator else None


def _identity_document(
    workspace_id: UUID,
    target: QualityOperationTarget,
    batch: QualityOperationBatch,
    collector_version: str,
    evaluation: QualityEvaluationOperationContext,
) -> dict[str, object]:
    provider = batch.provider
    return {
        "workspace_id": str(workspace_id),
        "evaluation_run_id": str(evaluation.evaluation_run_id),
        "evaluation_result_digest": evaluation.evaluation_result_digest,
        "dataset_version_id": str(evaluation.dataset_version_id),
        "dataset_digest": evaluation.dataset_digest,
        "service_id": str(evaluation.service_id),
        "agent_release_id": str(evaluation.agent_release_id),
        "run_configuration_digest": evaluation.run_configuration_digest,
        "gate_stage": target.gate_stage,
        "evidence_kind": batch.evidence_kind,
        "collector_version": collector_version,
        "provider_id": str(provider.provider_id) if provider is not None else None,
        "provider_configuration_version": (
            provider.provider_configuration_version if provider is not None else None
        ),
        "model_id": provider.model_id if provider is not None else None,
        "model_parameters_digest": target.model_parameters_digest,
        "network_region": target.network_region,
        "window_started_at": target.window_started_at.isoformat(),
        "window_ended_at": target.window_ended_at.isoformat(),
    }


def _source_document(item: QualityOperationSourceResult) -> dict[str, object]:
    return {
        "source": item.source,
        "status": item.status,
        "sample_count": item.sample_count,
        "citation_claim_count": item.citation_claim_count,
        "valid_citation_count": item.valid_citation_count,
        "supported_citation_count": item.supported_citation_count,
        "citation_presence_rate_bps": item.citation_presence_rate_bps,
        "citation_support_rate_bps": item.citation_support_rate_bps,
        "answer_evaluated_count": item.answer_evaluated_count,
        "acceptable_answer_count": item.acceptable_answer_count,
        "answer_acceptance_rate_bps": item.answer_acceptance_rate_bps,
        "feedback_count": item.feedback_count,
        "positive_feedback_count": item.positive_feedback_count,
        "positive_feedback_rate_bps": item.positive_feedback_rate_bps,
        "tool_call_count": item.tool_call_count,
        "unauthorized_access_count": item.unauthorized_access_count,
        "restricted_field_leakage_count": item.restricted_field_leakage_count,
        "unauthorized_tool_call_count": item.unauthorized_tool_call_count,
        "reason_codes": item.reason_codes,
        "evidence_digest": item.evidence_digest,
    }


def _digest_document(document: object) -> str:
    try:
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise QualityValidationError from error
    return hashlib.sha256(payload).hexdigest()
