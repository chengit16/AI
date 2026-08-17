"""编排六层质量评估、身份漂移检测和只含摘要的不可变报告。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid5

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.quality.application.errors import (
    QualityConflictError,
    QualityDeniedError,
    QualityValidationError,
)
from ai_platform_api.modules.quality.application.evaluation_policy import (
    QUALITY_EVALUATION_LAYERS,
    QUALITY_EVALUATION_POLICY,
)
from ai_platform_api.modules.quality.domain.evaluation import (
    QualityEvaluationBatch,
    QualityEvaluationExecutionRequest,
    QualityEvaluationExecutor,
    QualityEvaluationLayer,
    QualityEvaluationLayerResult,
    QualityEvaluationObservation,
    QualityEvaluationPolicy,
    QualityEvaluationReport,
    QualityEvaluationRun,
    QualityEvaluationSampleResult,
    QualityEvaluationStatus,
    QualityEvaluationTarget,
    QualityEvaluationUnitOfWork,
)

EVALUATION_NAMESPACE = UUID("55000000-0000-4000-8000-000000000513")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EVALUATE_PERMISSION = "agent.test.execute"
READ_PERMISSIONS = frozenset({"agent.test.read", "operations.records.read"})


class QualityEvaluationService:
    """执行固定六层评估，并按运行身份保证结果幂等且不可漂移。"""

    def __init__(
        self,
        unit_of_work: QualityEvaluationUnitOfWork,
        executor: QualityEvaluationExecutor,
        policy: QualityEvaluationPolicy = QUALITY_EVALUATION_POLICY,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._executor = executor
        self._policy = policy

    def evaluate(
        self,
        context: RequestContext,
        target: QualityEvaluationTarget,
    ) -> QualityEvaluationReport:
        """读取不可变数据集、执行内部评估，并原子写入报告。"""

        # 1. 先在短事务内读取权威数据集身份，跨空间标识始终表现为不可见。
        _require_evaluate_authorization(context)
        with self._unit_of_work as unit_of_work:
            snapshot = unit_of_work.evaluations.get_dataset_snapshot(
                context.workspace_id,
                target.dataset_version_id,
            )
        if snapshot is None:
            raise QualityValidationError
        dataset_digest, sample_version_ids = snapshot

        # 2. 身份已漂移时不执行未知目标；仍生成低敏失败事实，保留运营归因能力。
        can_execute = (
            target.dataset_digest == dataset_digest
            and self._executor.evaluator_version == self._policy.evaluator_version
        )
        if can_execute:
            request = QualityEvaluationExecutionRequest(
                context.workspace_id,
                target,
                sample_version_ids,
            )
            try:
                batch = self._executor.evaluate(request)
            except TimeoutError:
                batch = _timeout_batch(request, self._executor.evaluator_version)
        else:
            batch = QualityEvaluationBatch(
                context.workspace_id,
                target,
                self._executor.evaluator_version,
                (),
            )
        report = build_quality_evaluation_report(
            workspace_id=context.workspace_id,
            authoritative_dataset_digest=dataset_digest,
            member_sample_ids=sample_version_ids,
            target=target,
            batch=batch,
            policy=self._policy,
            actor_id=context.actor_id,
            completed_at=datetime.now(UTC),
        )
        return self._persist(report)

    def _persist(self, report: QualityEvaluationReport) -> QualityEvaluationReport:
        """串行化相同运行身份；同身份结果漂移必须显式冲突。"""

        with self._unit_of_work as unit_of_work:
            unit_of_work.evaluations.lock_run_identity(report.run.run_identity_digest)
            existing = unit_of_work.evaluations.get_report_by_identity(
                report.run.workspace_id,
                report.run.run_identity_digest,
            )
            if existing is not None:
                if existing.run.result_digest != report.run.result_digest:
                    raise QualityConflictError
                return existing
            unit_of_work.evaluations.add_report(report)
            unit_of_work.commit()
            return report

    def get_report(
        self,
        context: RequestContext,
        evaluation_run_id: UUID,
    ) -> QualityEvaluationReport | None:
        """按工作空间读取报告；集合读取要求完整空间授权。"""

        _require_read_authorization(context)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.evaluations.get_report(
                context.workspace_id,
                evaluation_run_id,
            )


def build_quality_evaluation_report(
    *,
    workspace_id: UUID,
    authoritative_dataset_digest: str,
    member_sample_ids: tuple[UUID, ...],
    target: QualityEvaluationTarget,
    batch: QualityEvaluationBatch,
    policy: QualityEvaluationPolicy,
    actor_id: UUID,
    completed_at: datetime,
) -> QualityEvaluationReport:
    """规范内部观测，执行六层失败关闭聚合并生成确定性摘要。"""

    # 1. 运行身份只绑定冻结目标和策略，不把完成时间或瞬时证据纳入幂等键。
    _validate_target(target, authoritative_dataset_digest, completed_at)
    identity_reasons = _identity_reasons(
        workspace_id,
        authoritative_dataset_digest,
        target,
        batch,
        policy,
    )
    run_identity_digest = _digest_document(
        {
            "workspace_id": str(workspace_id),
            "dataset_version_id": str(target.dataset_version_id),
            "dataset_digest": target.dataset_digest,
            "service_id": str(target.service_id),
            "agent_release_id": str(target.agent_release_id),
            "run_configuration_digest": target.run_configuration_digest,
            "policy_version_id": str(policy.policy_version_id),
            "policy_digest": policy.policy_digest,
        }
    )
    run_id = uuid5(EVALUATION_NAMESPACE, f"run:{run_identity_digest}")
    sample_results, integrity_reasons = _sample_results(
        run_id,
        workspace_id,
        member_sample_ids,
        batch.observations,
        policy,
    )
    # 2. 未知成员、重复观测和全部身份漂移都进入每一层，避免局部高分掩盖运行污染。
    identity_reasons.update(integrity_reasons)
    layer_results = _layer_results(
        run_id,
        workspace_id,
        sample_results,
        identity_reasons,
        policy,
    )
    report_reasons = tuple(
        sorted({reason for result in layer_results for reason in result.reason_codes})
    )
    outcomes = [item.outcome for item in sample_results]
    status: QualityEvaluationStatus = (
        "passed" if all(item.status == "passed" for item in layer_results) else "failed"
    )
    evaluator_identity_digest = _digest_document({"evaluator_version": batch.evaluator_version})
    # 3. 最终摘要只覆盖运行、层和样本低敏事实，证据正文已经转换为 SHA-256。
    result_digest = _digest_document(
        {
            "run_identity_digest": run_identity_digest,
            "evaluator_identity_digest": evaluator_identity_digest,
            "status": status,
            "reason_codes": report_reasons,
            "layers": [_layer_document(item) for item in layer_results],
            "samples": [_sample_document(item) for item in sample_results],
        }
    )
    run = QualityEvaluationRun(
        evaluation_run_id=run_id,
        run_identity_digest=run_identity_digest,
        workspace_id=workspace_id,
        dataset_version_id=target.dataset_version_id,
        dataset_digest=target.dataset_digest,
        service_id=target.service_id,
        agent_release_id=target.agent_release_id,
        run_configuration_digest=target.run_configuration_digest,
        policy_version_id=policy.policy_version_id,
        policy_digest=policy.policy_digest,
        evaluator_version=batch.evaluator_version,
        evaluator_identity_digest=evaluator_identity_digest,
        status=status,
        observation_count=len(sample_results),
        passed_count=outcomes.count("passed"),
        failed_count=outcomes.count("failed"),
        timeout_count=outcomes.count("timeout"),
        skipped_count=outcomes.count("skipped"),
        reason_codes=report_reasons,
        result_digest=result_digest,
        created_by_actor_id=actor_id,
        completed_at=completed_at,
    )
    return QualityEvaluationReport(run, layer_results, sample_results)


def _sample_results(
    run_id: UUID,
    workspace_id: UUID,
    member_sample_ids: tuple[UUID, ...],
    observations: tuple[QualityEvaluationObservation, ...],
    policy: QualityEvaluationPolicy,
) -> tuple[tuple[QualityEvaluationSampleResult, ...], set[str]]:
    """过滤未知成员和重复观测，正文证据只转换为稳定摘要。"""

    # 1. 先验证成员与单层唯一性，异常观测不进入结果，但会留下稳定漂移原因。
    members = frozenset(member_sample_ids)
    allowed_by_layer = {item.layer: item.allowed_reason_codes for item in policy.layers}
    results: list[QualityEvaluationSampleResult] = []
    integrity_reasons: set[str] = set()
    seen: set[tuple[QualityEvaluationLayer, UUID]] = set()
    for observation in observations:
        if observation.layer not in allowed_by_layer:
            integrity_reasons.add("batch_integrity_drift")
            continue
        key = (observation.layer, observation.sample_version_id)
        if key in seen:
            integrity_reasons.add("batch_integrity_drift")
            continue
        seen.add(key)
        if observation.sample_version_id not in members:
            integrity_reasons.add("sample_identity_drift")
            continue
        reasons = tuple(sorted(set(observation.reason_codes)))
        _validate_observation(observation, reasons, allowed_by_layer[observation.layer])
        results.append(
            QualityEvaluationSampleResult(
                evaluation_run_id=run_id,
                workspace_id=workspace_id,
                sample_version_id=observation.sample_version_id,
                layer=observation.layer,
                outcome=observation.outcome,
                score_bps=observation.score_bps,
                duration_ms=observation.duration_ms,
                reason_codes=reasons,
                evidence_digest=_digest_document(observation.evidence),
            )
        )
    # 2. 固定按六层顺序和样本 UUID 排列，确保相同观测可复算相同报告摘要。
    order = {layer: position for position, layer in enumerate(QUALITY_EVALUATION_LAYERS)}
    return (
        tuple(sorted(results, key=lambda item: (order[item.layer], item.sample_version_id.hex))),
        integrity_reasons,
    )


def _layer_results(
    run_id: UUID,
    workspace_id: UUID,
    sample_results: tuple[QualityEvaluationSampleResult, ...],
    identity_reasons: set[str],
    policy: QualityEvaluationPolicy,
) -> tuple[QualityEvaluationLayerResult, ...]:
    """逐层聚合；安全硬失败和非通过状态不能被高分样本抵消。"""

    # 1. 每层独立计算样本数、状态分布和整数基点均分，不跨层稀释失败。
    results: list[QualityEvaluationLayerResult] = []
    for layer_policy in policy.layers:
        samples = tuple(item for item in sample_results if item.layer == layer_policy.layer)
        outcomes = [item.outcome for item in samples]
        reasons = set(identity_reasons)
        reasons.update(reason for item in samples for reason in item.reason_codes)
        if len(samples) < layer_policy.minimum_sample_count:
            reasons.add("sample_insufficient")
        if "failed" in outcomes:
            reasons.add("observation_failed")
        if "timeout" in outcomes:
            reasons.add("observation_timeout")
        if "skipped" in outcomes:
            reasons.add("observation_skipped")
        score_bps = sum(item.score_bps for item in samples) // len(samples) if samples else 0
        if score_bps < layer_policy.minimum_score_bps:
            reasons.add("score_below_threshold")
        hard_failed = not policy.hard_failure_reason_codes.isdisjoint(reasons)
        passed = (
            len(samples) >= layer_policy.minimum_sample_count
            and all(item.outcome == "passed" for item in samples)
            and score_bps >= layer_policy.minimum_score_bps
            and not identity_reasons
            and not hard_failed
        )
        # 2. 层证据摘要仅覆盖样本结果摘要；身份漂移和硬失败直接决定本层失败。
        evidence_digest = _digest_document([_sample_document(item) for item in samples])
        results.append(
            QualityEvaluationLayerResult(
                run_id,
                workspace_id,
                layer_policy.layer,
                "passed" if passed else "failed",
                len(samples),
                outcomes.count("passed"),
                score_bps,
                tuple(sorted(reasons)),
                evidence_digest,
            )
        )
    return tuple(results)


def _identity_reasons(
    workspace_id: UUID,
    authoritative_dataset_digest: str,
    target: QualityEvaluationTarget,
    batch: QualityEvaluationBatch,
    policy: QualityEvaluationPolicy,
) -> set[str]:
    """比较执行前目标与执行器实际身份，每个维度独立给出稳定原因。"""

    reasons: set[str] = set()
    if batch.workspace_id != workspace_id:
        reasons.add("workspace_identity_drift")
    if (
        target.dataset_digest != authoritative_dataset_digest
        or batch.target.dataset_version_id != target.dataset_version_id
        or batch.target.dataset_digest != target.dataset_digest
    ):
        reasons.add("dataset_identity_drift")
    if batch.target.service_id != target.service_id:
        reasons.add("service_identity_drift")
    if batch.target.agent_release_id != target.agent_release_id:
        reasons.add("release_identity_drift")
    if batch.target.run_configuration_digest != target.run_configuration_digest:
        reasons.add("configuration_identity_drift")
    if batch.evaluator_version != policy.evaluator_version:
        reasons.add("evaluator_identity_drift")
    return reasons


def _validate_target(
    target: QualityEvaluationTarget,
    authoritative_dataset_digest: str,
    completed_at: datetime,
) -> None:
    if (
        DIGEST_PATTERN.fullmatch(target.dataset_digest) is None
        or DIGEST_PATTERN.fullmatch(authoritative_dataset_digest) is None
        or DIGEST_PATTERN.fullmatch(target.run_configuration_digest) is None
        or completed_at.tzinfo is None
    ):
        raise QualityValidationError


def _validate_observation(
    observation: QualityEvaluationObservation,
    reasons: tuple[str, ...],
    allowed_reasons: frozenset[str],
) -> None:
    valid_score = (
        isinstance(observation.score_bps, int)
        and not isinstance(observation.score_bps, bool)
        and 0 <= observation.score_bps <= 10_000
    )
    valid_duration = (
        isinstance(observation.duration_ms, int)
        and not isinstance(observation.duration_ms, bool)
        and 0 <= observation.duration_ms <= 3_600_000
    )
    if (
        not valid_score
        or not valid_duration
        or len(reasons) > 8
        or not set(reasons).issubset(allowed_reasons)
        or (observation.outcome == "passed" and reasons)
        or (observation.outcome != "passed" and not reasons)
    ):
        raise QualityValidationError


def _require_evaluate_authorization(context: RequestContext) -> None:
    if (
        context.audit_authorization is None
        or context.authorized_permission_code != EVALUATE_PERMISSION
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


def _timeout_batch(
    request: QualityEvaluationExecutionRequest,
    evaluator_version: str,
) -> QualityEvaluationBatch:
    observations = tuple(
        QualityEvaluationObservation(
            sample_version_id,
            layer,
            "timeout",
            0,
            0,
            ("evaluation_timeout",),
            {},
        )
        for layer in QUALITY_EVALUATION_LAYERS
        for sample_version_id in request.sample_version_ids
    )
    return QualityEvaluationBatch(
        request.workspace_id,
        request.target,
        evaluator_version,
        observations,
    )


def _sample_document(item: QualityEvaluationSampleResult) -> dict[str, object]:
    return {
        "sample_version_id": str(item.sample_version_id),
        "layer": item.layer,
        "outcome": item.outcome,
        "score_bps": item.score_bps,
        "duration_ms": item.duration_ms,
        "reason_codes": item.reason_codes,
        "evidence_digest": item.evidence_digest,
    }


def _layer_document(item: QualityEvaluationLayerResult) -> dict[str, object]:
    return {
        "layer": item.layer,
        "status": item.status,
        "sample_count": item.sample_count,
        "passed_count": item.passed_count,
        "score_bps": item.score_bps,
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
