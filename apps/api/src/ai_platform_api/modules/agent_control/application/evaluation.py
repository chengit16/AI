"""创建固定测试集并编排 Agent 候选的确定性自动评估。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid5

from ai_platform_backend.integration.domain import AuditRecord
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.configuration import (
    parse_agent_configuration,
    reject_credentials,
    validate_configuration_references,
)
from ai_platform_api.modules.agent_control.application.errors import (
    AgentDeniedError,
    AgentLifecycleConflictError,
    AgentNotFoundError,
    AgentTestGateFailedError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.application.support import (
    browser_account,
    canonical_json,
    record_change,
    require_custom_agent,
    require_resource_scope,
)
from ai_platform_api.modules.agent_control.domain.evaluation import (
    HARD_GATE_CHECKS,
    REQUIRED_EVALUATION_CHECKS,
    AgentEvaluationCaseResult,
    AgentEvaluationCheckResult,
    AgentEvaluationDatasetVersion,
    AgentEvaluationExecutor,
    AgentEvaluationObservation,
    AgentEvaluationPolicyVersion,
    AgentEvaluationReport,
    AgentEvaluationRequest,
    AgentEvaluationRun,
    AgentEvaluationTestCase,
    EvaluationCheckCode,
    EvaluationOutcome,
)
from ai_platform_api.modules.agent_control.domain.models import (
    AgentControlUnitOfWork,
    AgentReleaseCandidate,
    AgentWriteConflictError,
)

EVALUATION_NAMESPACE = UUID("ac000000-0000-4000-8000-000000000304")
DEFAULT_EVALUATION_POLICY_ID = UUID("ac000000-0000-4000-8000-000000000001")
DATASET_VERSION_PATTERN = re.compile(r"^p304-[a-z0-9][a-z0-9-]{1,60}-v[1-9][0-9]*$")
EVALUATOR_VERSION_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
MAX_DATASET_BYTES = 512 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024


class EvaluationCaseDocument(BaseModel):
    """校验调用方提交的合成测试用例，不接受未登记扩展字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,79}$")
    check_code: Literal[
        "functional",
        "authorization",
        "prompt_injection",
        "citation",
        "output_contract",
    ]
    input_fixture: dict[str, object]
    expected_fixture: dict[str, object]
    timeout_ms: int = Field(ge=1, le=120_000)
    minimum_score_bps: int = Field(ge=0, le=10_000)


class EvaluationDatasetDocument(BaseModel):
    """确保版本化测试集覆盖全部必需检查且用例身份不重复。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=120)
    dataset_version: str
    cases: tuple[EvaluationCaseDocument, ...] = Field(min_length=5, max_length=200)

    @model_validator(mode="after")
    def validate_dataset(self) -> EvaluationDatasetDocument:
        if DATASET_VERSION_PATTERN.fullmatch(self.dataset_version) is None:
            raise ValueError("测试集版本格式无效")
        case_keys = [item.case_key for item in self.cases]
        if len(case_keys) != len(set(case_keys)):
            raise ValueError("测试用例标识重复")
        if set(item.check_code for item in self.cases) != set(REQUIRED_EVALUATION_CHECKS):
            raise ValueError("测试集必须且只能覆盖五类必需检查")
        return self


@dataclass(frozen=True)
class _LoadedEvaluation:
    """保存事务外执行所需的不可变输入和候选并发版本。"""

    request: AgentEvaluationRequest
    policy: AgentEvaluationPolicyVersion
    candidate_version: int
    agent_id: UUID
    existing_report: AgentEvaluationReport | None


@dataclass(frozen=True)
class _CaseFact:
    """在生成运行 ID 前暂存单用例的规范化结果。"""

    case_id: UUID
    check_code: EvaluationCheckCode
    outcome: EvaluationOutcome
    score_bps: int
    duration_ms: int
    evidence_hash: str


def create_evaluation_dataset(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    name: str,
    dataset_version: str,
    cases: tuple[dict[str, object], ...],
) -> AgentEvaluationDatasetVersion:
    """创建内容寻址的不可变合成测试集，相同版本不得指向不同内容。"""

    account_id = browser_account(context)
    if not context.authorized_workspace:
        raise AgentDeniedError
    dataset, test_cases = parse_evaluation_dataset(
        context.workspace_id,
        account_id,
        name=name,
        dataset_version=dataset_version,
        cases=cases,
        created_at=datetime.now(UTC),
    )
    with unit_of_work_factory as unit_of_work:
        if unit_of_work.evaluation.add_dataset_version(dataset, test_cases):
            _record_dataset_creation(unit_of_work, context, dataset)
            unit_of_work.commit()
            return dataset
        existing = unit_of_work.evaluation.get_dataset_by_version(
            context.workspace_id,
            dataset_version,
        )
        if existing is None or existing.dataset_hash != dataset.dataset_hash:
            raise AgentValidationError
        return existing


def parse_evaluation_dataset(
    workspace_id: UUID,
    account_id: UUID,
    *,
    name: str,
    dataset_version: str,
    cases: tuple[dict[str, object], ...],
    created_at: datetime,
) -> tuple[AgentEvaluationDatasetVersion, tuple[AgentEvaluationTestCase, ...]]:
    """规范化测试集顺序、执行敏感信息扫描并生成稳定版本摘要。"""

    # 1. 严格解析并按用例键排序，使调用方顺序不会改变测试集身份。
    try:
        document = EvaluationDatasetDocument.model_validate(
            {
                "name": name.strip(),
                "dataset_version": dataset_version,
                "cases": cases,
            }
        )
    except ValidationError as error:
        raise AgentValidationError from error
    ordered = tuple(sorted(document.cases, key=lambda item: item.case_key))
    normalized_cases = tuple(item.model_dump(mode="json") for item in ordered)
    for item in normalized_cases:
        reject_credentials(item["input_fixture"])
        reject_credentials(item["expected_fixture"])
    # 2. 对规范文档做大小限制和内容寻址，再派生每个用例的稳定身份。
    dataset_document = {
        "schema_version": 1,
        "name": document.name,
        "dataset_version": document.dataset_version,
        "cases": normalized_cases,
    }
    payload = canonical_json(dataset_document)
    if len(payload) > MAX_DATASET_BYTES:
        raise AgentValidationError
    dataset_hash = hashlib.sha256(payload).hexdigest()
    dataset_id = uuid5(
        EVALUATION_NAMESPACE,
        f"dataset:{workspace_id}:{document.dataset_version}:{dataset_hash}",
    )
    dataset = AgentEvaluationDatasetVersion(
        dataset_version_id=dataset_id,
        workspace_id=workspace_id,
        name=document.name,
        dataset_version=document.dataset_version,
        dataset_hash=dataset_hash,
        created_by_account_id=account_id,
        created_at=created_at,
    )
    test_cases = tuple(
        _test_case(dataset, item, position)
        for position, item in enumerate(normalized_cases, start=1)
    )
    return dataset, test_cases


def run_agent_evaluation(
    unit_of_work_factory: AgentControlUnitOfWork,
    executor: AgentEvaluationExecutor,
    context: RequestContext,
    *,
    candidate_id: UUID,
    dataset_version_id: UUID,
) -> AgentEvaluationReport:
    """在短事务之间执行固定测试，并原子保存结果、候选状态、审计和 Outbox。"""

    account_id = browser_account(context)
    evaluator_version = executor.evaluator_version
    if EVALUATOR_VERSION_PATTERN.fullmatch(evaluator_version) is None:
        raise AgentValidationError

    # 1. 先读取不可变候选、配置、测试集和策略；模型或规则执行期间不占数据库事务。
    loaded = _load_evaluation(
        unit_of_work_factory,
        context,
        candidate_id=candidate_id,
        dataset_version_id=dataset_version_id,
    )
    if loaded.existing_report is not None:
        return loaded.existing_report
    try:
        observations = executor.evaluate(loaded.request)
    except TimeoutError:
        observations = tuple(
            AgentEvaluationObservation(case.case_id, "timeout", 0, case.timeout_ms, {})
            for case in loaded.request.cases
        )
    report = build_evaluation_report(
        loaded.request,
        loaded.policy,
        observations,
        evaluator_version=evaluator_version,
        account_id=account_id,
        completed_at=datetime.now(UTC),
    )

    # 2. 写事务再次核对候选身份；并发变化、非确定性重跑或状态越界均失败关闭。
    try:
        with unit_of_work_factory as unit_of_work:
            current = unit_of_work.agents.get_candidate(context.workspace_id, candidate_id)
            if current is None:
                raise AgentNotFoundError
            existing = unit_of_work.evaluation.get_report_by_identity(
                context.workspace_id,
                candidate_id,
                dataset_version_id,
                loaded.policy.evaluation_policy_version_id,
            )
            if existing is not None:
                if existing.run.result_hash != report.run.result_hash:
                    raise AgentLifecycleConflictError
                return existing
            _require_same_candidate(current, loaded.request, loaded.candidate_version)
            unit_of_work.evaluation.add_report(report)
            next_status: Literal["test_failed", "ready_for_approval"] = (
                "ready_for_approval" if report.run.status == "passed" else "test_failed"
            )
            if not unit_of_work.evaluation.transition_candidate_after_evaluation(
                context.workspace_id,
                candidate_id,
                expected_version=current.version,
                next_status=next_status,
                updated_at=report.run.completed_at,
            ):
                raise AgentLifecycleConflictError
            # 3. 结果、候选状态、脱敏审计和 Outbox 必须作为同一个提交成功或回滚。
            _record_evaluation_completion(
                unit_of_work,
                context,
                report,
                agent_id=loaded.agent_id,
                aggregate_version=current.version + 1,
            )
            unit_of_work.commit()
            return report
    except AgentWriteConflictError as error:
        recovered = _recover_report(unit_of_work_factory, context, report)
        if recovered is not None:
            return recovered
        raise AgentLifecycleConflictError from error


def build_evaluation_report(
    request: AgentEvaluationRequest,
    policy: AgentEvaluationPolicyVersion,
    observations: tuple[AgentEvaluationObservation, ...],
    *,
    evaluator_version: str,
    account_id: UUID,
    completed_at: datetime,
) -> AgentEvaluationReport:
    """把执行器观测规范化为可复算结果，硬门禁不允许被平均质量分抵消。"""

    # 1. 先校验不可变策略，再把缺失、超时和低于用例阈值的观测统一折算为失败。
    validate_evaluation_policy(policy)
    if EVALUATOR_VERSION_PATTERN.fullmatch(evaluator_version) is None:
        raise AgentValidationError
    case_facts = _case_facts(request.cases, observations)
    check_facts = _check_facts(case_facts, policy)
    status: Literal["passed", "failed"] = (
        "passed" if all(item[1] == "passed" for item in check_facts) else "failed"
    )
    # 2. 结果摘要不包含完成时间，因此同候选、测试集、策略和观测可复算相同身份。
    result_document = {
        "schema_version": 1,
        "candidate_id": str(request.candidate_id),
        "candidate_hash": request.candidate_hash,
        "config_hash": request.config_hash,
        "dataset_version_id": str(request.dataset.dataset_version_id),
        "dataset_hash": request.dataset.dataset_hash,
        "evaluation_policy_version_id": str(policy.evaluation_policy_version_id),
        "policy_hash": policy.policy_hash,
        "evaluator_version": evaluator_version,
        "status": status,
        "cases": [_case_fact_document(value) for value in case_facts],
        "checks": [_check_fact_document(value) for value in check_facts],
    }
    result_hash = hashlib.sha256(canonical_json(result_document)).hexdigest()
    run_id = uuid5(
        EVALUATION_NAMESPACE,
        f"run:{request.candidate_id}:{request.dataset.dataset_version_id}:"
        f"{policy.evaluation_policy_version_id}:{result_hash}",
    )
    # 3. 摘要和运行身份确定后再构造只追加事实，避免循环依赖污染可复算内容。
    case_results = tuple(
        AgentEvaluationCaseResult(
            run_id,
            request.dataset.workspace_id,
            value.case_id,
            value.check_code,
            value.outcome,
            value.score_bps,
            value.duration_ms,
            value.evidence_hash,
        )
        for value in case_facts
    )
    check_results = tuple(
        AgentEvaluationCheckResult(
            run_id,
            request.dataset.workspace_id,
            check_code,
            check_status,
            case_count,
            passed_count,
            score_bps,
            evidence_hash,
        )
        for (
            check_code,
            check_status,
            case_count,
            passed_count,
            score_bps,
            evidence_hash,
        ) in check_facts
    )
    outcomes = [item.outcome for item in case_facts]
    run = AgentEvaluationRun(
        evaluation_run_id=run_id,
        candidate_id=request.candidate_id,
        workspace_id=request.dataset.workspace_id,
        dataset_version_id=request.dataset.dataset_version_id,
        evaluation_policy_version_id=policy.evaluation_policy_version_id,
        candidate_hash=request.candidate_hash,
        config_hash=request.config_hash,
        evaluator_version=evaluator_version,
        evidence_level="core_functional",
        status=status,
        total_cases=len(case_facts),
        passed_cases=outcomes.count("passed"),
        failed_cases=outcomes.count("failed"),
        timeout_cases=outcomes.count("timeout"),
        skipped_cases=outcomes.count("skipped"),
        result_hash=result_hash,
        created_by_account_id=account_id,
        completed_at=completed_at,
    )
    return AgentEvaluationReport(run, check_results, case_results)


def get_evaluation_report(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    candidate_id: UUID,
    evaluation_run_id: UUID,
) -> AgentEvaluationReport:
    """读取工作空间内的不可变评估报告，并执行 Agent 资源授权。"""

    browser_account(context)
    with unit_of_work_factory as unit_of_work:
        candidate = unit_of_work.agents.get_candidate(context.workspace_id, candidate_id)
        if candidate is None:
            raise AgentNotFoundError
        require_resource_scope(context, candidate.agent_id)
        report = unit_of_work.evaluation.get_report(context.workspace_id, evaluation_run_id)
        if report is None or report.run.candidate_id != candidate_id:
            raise AgentNotFoundError
        return report


def require_passing_evaluation(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    candidate_id: UUID,
) -> AgentEvaluationReport:
    """返回与候选摘要匹配的最新通过证据，供后续审批入口失败关闭。"""

    browser_account(context)
    with unit_of_work_factory as unit_of_work:
        candidate = unit_of_work.agents.get_candidate(context.workspace_id, candidate_id)
        if candidate is None:
            raise AgentNotFoundError
        require_resource_scope(context, candidate.agent_id)
        report = unit_of_work.evaluation.get_latest_passing_report(
            context.workspace_id,
            candidate_id,
        )
        if (
            candidate.status != "ready_for_approval"
            or report is None
            or report.run.candidate_hash != candidate.candidate_hash
            or report.run.config_hash != candidate.config_hash
            or set(item.check_code for item in report.checks) != set(REQUIRED_EVALUATION_CHECKS)
            or any(item.status != "passed" for item in report.checks)
        ):
            raise AgentTestGateFailedError
        return report


def evaluation_policy_digest(policy: AgentEvaluationPolicyVersion) -> str:
    """计算固定策略的规范摘要，防止数据库字段被局部篡改。"""

    document = {
        "schema_version": 1,
        "policy_key": policy.policy_key,
        "version_number": policy.version_number,
        "required_check_codes": list(policy.required_check_codes),
        "hard_gate_check_codes": list(policy.hard_gate_check_codes),
        "minimum_check_scores": dict(policy.minimum_check_scores),
        "failure_handling": policy.failure_handling,
        "timeout_handling": policy.timeout_handling,
        "skipped_handling": policy.skipped_handling,
        "evaluator_kind": policy.evaluator_kind,
        "online_llm_grading": policy.online_llm_grading,
        "multimodal_image_qa": policy.multimodal_image_qa,
    }
    return hashlib.sha256(canonical_json(document)).hexdigest()


def validate_evaluation_policy(policy: AgentEvaluationPolicyVersion) -> None:
    """拒绝缺少硬门禁、允许跳过或启用后置能力的损坏策略。"""

    try:
        thresholds = tuple(policy.minimum_score(code) for code in REQUIRED_EVALUATION_CHECKS)
    except ValueError as error:
        raise AgentTestGateFailedError from error
    if (
        policy.evaluation_policy_version_id != DEFAULT_EVALUATION_POLICY_ID
        or policy.status != "active"
        or policy.required_check_codes != REQUIRED_EVALUATION_CHECKS
        or policy.hard_gate_check_codes != HARD_GATE_CHECKS
        or any(score < 0 or score > 10_000 for score in thresholds)
        or any(policy.minimum_score(code) != 10_000 for code in HARD_GATE_CHECKS)
        or policy.failure_handling != "block_release"
        or policy.timeout_handling != "count_as_failure"
        or policy.skipped_handling != "count_as_failure"
        or policy.evaluator_kind != "deterministic_rules"
        or policy.online_llm_grading
        or policy.multimodal_image_qa
        or policy.policy_hash != evaluation_policy_digest(policy)
    ):
        raise AgentTestGateFailedError


def _load_evaluation(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    candidate_id: UUID,
    dataset_version_id: UUID,
) -> _LoadedEvaluation:
    """读取并复核执行输入；失效配置或损坏版本不能进入执行器。"""

    with unit_of_work_factory as unit_of_work:
        # 1. 候选、Agent 和历史 revision 必须来自当前工作空间且保持来源摘要一致。
        candidate = unit_of_work.agents.get_candidate(context.workspace_id, candidate_id)
        if candidate is None:
            raise AgentNotFoundError
        require_resource_scope(context, candidate.agent_id)
        agent = require_custom_agent(
            unit_of_work,
            context.workspace_id,
            candidate.agent_id,
        )
        if agent.status != "active" or candidate.status not in {
            "created",
            "test_failed",
            "ready_for_approval",
        }:
            raise AgentLifecycleConflictError
        revision = unit_of_work.agents.get_draft_revision(
            context.workspace_id,
            candidate.draft_id,
            candidate.draft_revision,
        )
        if revision is None or revision.config_hash != candidate.config_hash:
            raise AgentLifecycleConflictError
        parsed, normalized, config_hash = parse_agent_configuration(revision.configuration)
        validate_configuration_references(unit_of_work.configuration, context, parsed)
        if normalized != revision.configuration or config_hash != candidate.config_hash:
            raise AgentLifecycleConflictError
        # 2. 测试集和策略均按不可变事实复算摘要，损坏或缺失时不调用执行器。
        dataset = unit_of_work.evaluation.get_dataset_version(
            context.workspace_id,
            dataset_version_id,
            for_share=True,
        )
        cases = unit_of_work.evaluation.get_dataset_cases(
            context.workspace_id,
            dataset_version_id,
            for_share=True,
        )
        policy = unit_of_work.evaluation.get_active_policy(for_share=True)
        if dataset is None or policy is None:
            raise AgentTestGateFailedError
        _validate_dataset_facts(dataset, cases)
        validate_evaluation_policy(policy)
        existing_report = unit_of_work.evaluation.get_report_by_identity(
            context.workspace_id,
            candidate_id,
            dataset_version_id,
            policy.evaluation_policy_version_id,
        )
        return _LoadedEvaluation(
            AgentEvaluationRequest(
                candidate.candidate_id,
                candidate.candidate_hash,
                candidate.config_hash,
                normalized,
                dataset,
                cases,
            ),
            policy,
            candidate.version,
            candidate.agent_id,
            existing_report,
        )


def _test_case(
    dataset: AgentEvaluationDatasetVersion,
    document: dict[str, object],
    position: int,
) -> AgentEvaluationTestCase:
    case_hash = hashlib.sha256(canonical_json(document)).hexdigest()
    case_key = str(document["case_key"])
    timeout_ms = document["timeout_ms"]
    minimum_score_bps = document["minimum_score_bps"]
    if (
        not isinstance(timeout_ms, int)
        or isinstance(timeout_ms, bool)
        or not isinstance(minimum_score_bps, int)
        or isinstance(minimum_score_bps, bool)
    ):
        raise AgentValidationError
    return AgentEvaluationTestCase(
        case_id=uuid5(dataset.dataset_version_id, case_key),
        dataset_version_id=dataset.dataset_version_id,
        workspace_id=dataset.workspace_id,
        position=position,
        case_key=case_key,
        check_code=cast(EvaluationCheckCode, document["check_code"]),
        input_fixture=cast(dict[str, object], document["input_fixture"]),
        expected_fixture=cast(dict[str, object], document["expected_fixture"]),
        timeout_ms=timeout_ms,
        minimum_score_bps=minimum_score_bps,
        case_hash=case_hash,
    )


def _validate_dataset_facts(
    dataset: AgentEvaluationDatasetVersion,
    cases: tuple[AgentEvaluationTestCase, ...],
) -> None:
    normalized = tuple(
        {
            "case_key": item.case_key,
            "check_code": item.check_code,
            "input_fixture": item.input_fixture,
            "expected_fixture": item.expected_fixture,
            "timeout_ms": item.timeout_ms,
            "minimum_score_bps": item.minimum_score_bps,
        }
        for item in cases
    )
    document = {
        "schema_version": 1,
        "name": dataset.name,
        "dataset_version": dataset.dataset_version,
        "cases": normalized,
    }
    if (
        tuple(item.position for item in cases) != tuple(range(1, len(cases) + 1))
        or tuple(item.case_key for item in cases) != tuple(sorted(item.case_key for item in cases))
        or len({item.case_id for item in cases}) != len(cases)
        or set(item.check_code for item in cases) != set(REQUIRED_EVALUATION_CHECKS)
        or any(
            item.workspace_id != dataset.workspace_id
            or item.dataset_version_id != dataset.dataset_version_id
            or item.case_hash != hashlib.sha256(canonical_json(value)).hexdigest()
            for item, value in zip(cases, normalized, strict=True)
        )
        or dataset.dataset_hash != hashlib.sha256(canonical_json(document)).hexdigest()
    ):
        raise AgentTestGateFailedError


def _case_facts(
    cases: tuple[AgentEvaluationTestCase, ...],
    observations: tuple[AgentEvaluationObservation, ...],
) -> tuple[_CaseFact, ...]:
    expected_ids = {item.case_id for item in cases}
    observed_ids = [item.case_id for item in observations]
    if len(observed_ids) != len(set(observed_ids)) or not set(observed_ids) <= expected_ids:
        raise AgentValidationError
    by_id = {item.case_id: item for item in observations}
    facts: list[_CaseFact] = []
    for case in cases:
        observation = by_id.get(case.case_id)
        if observation is None:
            observation = AgentEvaluationObservation(case.case_id, "skipped", 0, 0, {})
        facts.append(_normalize_observation(case, observation))
    return tuple(facts)


def _normalize_observation(
    case: AgentEvaluationTestCase,
    observation: AgentEvaluationObservation,
) -> _CaseFact:
    if (
        isinstance(observation.score_bps, bool)
        or not 0 <= observation.score_bps <= 10_000
        or isinstance(observation.duration_ms, bool)
        or observation.duration_ms < 0
    ):
        raise AgentValidationError
    outcome = observation.outcome
    score = observation.score_bps
    if observation.duration_ms > case.timeout_ms:
        outcome, score = "timeout", 0
    elif outcome != "passed" or score < case.minimum_score_bps:
        outcome, score = ("failed" if outcome == "passed" else outcome), 0
    evidence_document = {
        "case_id": str(case.case_id),
        "case_hash": case.case_hash,
        "outcome": outcome,
        "score_bps": score,
        "duration_ms": observation.duration_ms,
        "evidence": observation.evidence,
    }
    payload = canonical_json(evidence_document)
    if len(payload) > MAX_EVIDENCE_BYTES:
        raise AgentValidationError
    return _CaseFact(
        case.case_id,
        case.check_code,
        outcome,
        score,
        observation.duration_ms,
        hashlib.sha256(payload).hexdigest(),
    )


def _check_facts(
    case_facts: tuple[_CaseFact, ...],
    policy: AgentEvaluationPolicyVersion,
) -> tuple[tuple[EvaluationCheckCode, Literal["passed", "failed"], int, int, int, str], ...]:
    facts = []
    for check_code in policy.required_check_codes:
        matching = tuple(item for item in case_facts if item.check_code == check_code)
        if not matching:
            raise AgentTestGateFailedError
        passed_count = sum(item.outcome == "passed" for item in matching)
        score = sum(item.score_bps for item in matching) // len(matching)
        status: Literal["passed", "failed"] = (
            "passed"
            if passed_count == len(matching) and score >= policy.minimum_score(check_code)
            else "failed"
        )
        # 安全检查必须逐例满分；平均分不能掩盖单个越权、注入、引用或结构失败。
        if check_code in policy.hard_gate_check_codes and any(
            item.score_bps != 10_000 for item in matching
        ):
            status = "failed"
        evidence_hash = hashlib.sha256(
            canonical_json([item.evidence_hash for item in matching])
        ).hexdigest()
        facts.append((check_code, status, len(matching), passed_count, score, evidence_hash))
    return tuple(facts)


def _require_same_candidate(
    candidate: AgentReleaseCandidate,
    request: AgentEvaluationRequest,
    expected_version: int,
) -> None:
    if (
        candidate.version != expected_version
        or candidate.candidate_hash != request.candidate_hash
        or candidate.config_hash != request.config_hash
        or candidate.status not in {"created", "test_failed", "ready_for_approval"}
    ):
        raise AgentLifecycleConflictError


def _recover_report(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    expected: AgentEvaluationReport,
) -> AgentEvaluationReport | None:
    with unit_of_work_factory as unit_of_work:
        report = unit_of_work.evaluation.get_report_by_identity(
            context.workspace_id,
            expected.run.candidate_id,
            expected.run.dataset_version_id,
            expected.run.evaluation_policy_version_id,
        )
        if report is None:
            return None
        if report.run.result_hash != expected.run.result_hash:
            raise AgentLifecycleConflictError
        return report


def _record_dataset_creation(
    unit_of_work: AgentControlUnitOfWork,
    context: RequestContext,
    dataset: AgentEvaluationDatasetVersion,
) -> None:
    """测试用例正文不进入审计，只记录版本、身份和摘要。"""

    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid5(
                EVALUATION_NAMESPACE, f"audit:{context.request_id}:{dataset.dataset_hash}"
            ),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="agent.evaluation_dataset.created",
            resource_type="agent_evaluation_dataset",
            resource_id=dataset.dataset_version_id,
            outcome="succeeded",
            occurred_at=dataset.created_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes={
                "dataset_version": dataset.dataset_version,
                "dataset_hash": dataset.dataset_hash,
            },
        )
    )


def _record_evaluation_completion(
    unit_of_work: AgentControlUnitOfWork,
    context: RequestContext,
    report: AgentEvaluationReport,
    *,
    agent_id: UUID,
    aggregate_version: int,
) -> None:
    record_change(
        unit_of_work,
        context,
        action="agent.test.completed",
        event_type="agent.test.completed",
        resource_id=agent_id,
        aggregate_version=aggregate_version,
        occurred_at=report.run.completed_at,
        attributes={
            "candidate_id": str(report.run.candidate_id),
            "evaluation_run_id": str(report.run.evaluation_run_id),
            "dataset_version_id": str(report.run.dataset_version_id),
            "evaluation_policy_version_id": str(report.run.evaluation_policy_version_id),
            "status": report.run.status,
            "result_hash": report.run.result_hash,
            "evaluator_version": report.run.evaluator_version,
            "evidence_level": report.run.evidence_level,
            "total_cases": report.run.total_cases,
            "passed_cases": report.run.passed_cases,
            "failed_cases": report.run.failed_cases,
            "timeout_cases": report.run.timeout_cases,
            "skipped_cases": report.run.skipped_cases,
        },
    )


def _case_fact_document(value: _CaseFact) -> dict[str, object]:
    return {
        "case_id": str(value.case_id),
        "check_code": value.check_code,
        "outcome": value.outcome,
        "score_bps": value.score_bps,
        "duration_ms": value.duration_ms,
        "evidence_hash": value.evidence_hash,
    }


def _check_fact_document(
    value: tuple[EvaluationCheckCode, Literal["passed", "failed"], int, int, int, str],
) -> dict[str, object]:
    return {
        "check_code": value[0],
        "status": value[1],
        "case_count": value[2],
        "passed_count": value[3],
        "score_bps": value[4],
        "evidence_hash": value[5],
    }
