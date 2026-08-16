"""把模型候选意图收敛为 Release 允许且当前获授权的原子执行计划。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.policy import PolicyDecision
from ai_platform_api.modules.tool_execution.application.definitions import (
    verify_tool_definition,
)
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolExecutionDeniedError,
    ToolResultRejectedError,
    ToolRunBudgetExceededError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.domain.catalog import ToolDefinition
from ai_platform_api.modules.tool_execution.domain.planning import (
    CandidateToolIntent,
    FrozenToolPlanStep,
    ReleaseToolReference,
    ToolExecutionPlan,
    ToolPlanStore,
    ToolPolicyDecisionRecord,
    ToolReleasePlan,
    ToolReleasePlanSource,
)
from ai_platform_api.modules.tool_execution.domain.tasks import (
    ToolRun,
    ToolRunBudget,
    ToolStepBudget,
)

MAXIMUM_ARGUMENT_BYTES = 65_536
MAXIMUM_RESULT_BYTES = 262_144
TOOL_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
RESOURCE_ARGUMENTS = {
    "document.read_authorized_range": "document_id",
    "workflow.get_status": "workflow_run_id",
    "approval.get_status": "approval_instance_id",
}


class ToolPlanningCatalog(Protocol):
    """只暴露计划阶段需要的精确版本和当前策略决策。"""

    def authorize_available_tool(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        tool_id: UUID,
        tool_version: int,
        resource_id: UUID | None = None,
    ) -> tuple[ToolDefinition, PolicyDecision]: ...


class ToolRunReader(Protocol):
    """只暴露计划阶段读取可信 Run 所需的窄接口。"""

    def get_run(self, context: RequestContext, run_id: UUID) -> ToolRun: ...


class ToolExecutionPlanningService:
    """完成全量预检后一次冻结 Step，任何失败都不会调用 Adapter。"""

    def __init__(
        self,
        tasks: ToolRunReader,
        store: ToolPlanStore,
        catalog: ToolPlanningCatalog,
        releases: ToolReleasePlanSource,
    ) -> None:
        self._tasks = tasks
        self._store = store
        self._catalog = catalog
        self._releases = releases

    def freeze(
        self,
        context: RequestContext,
        run_id: UUID,
        intents: Sequence[CandidateToolIntent],
        *,
        evaluated_at: datetime | None = None,
    ) -> ToolExecutionPlan:
        """校验整个候选集合，并在一次数据库事务中发布只读执行计划。"""

        now = evaluated_at or datetime.now(UTC)
        run = self._tasks.get_run(context, run_id)
        if run.state != "pending":
            raise ToolRunConflictError
        if now >= run.deadline_at or not intents or len(intents) > run.budget.max_steps:
            raise ToolRunBudgetExceededError
        release = self._require_release(run.workspace_id, run.service_id, run.agent_release_id)
        allowed = _release_allowlist(release)

        # 1. 先完成 Release、Schema、预算和当前 PDP 全量预检，失败时 Store 尚未收到写命令。
        frozen_steps: list[FrozenToolPlanStep] = []
        for sequence_no, intent in enumerate(intents, start=1):
            reference = allowed.get((intent.tool_id, intent.tool_version))
            if reference is None or reference.access_mode != "read":
                raise ToolExecutionDeniedError
            definition, decision = self._catalog.authorize_available_tool(
                context,
                workspace_id=run.workspace_id,
                tool_id=intent.tool_id,
                tool_version=intent.tool_version,
                resource_id=_target_resource_id(intent),
            )
            _require_release_match(intent, reference.permission_code, definition)
            arguments_hash = _validate_and_hash_arguments(definition, intent.arguments)
            step_id = uuid4()
            policy = build_tool_policy_decision_record(
                decision,
                workspace_id=run.workspace_id,
                run_id=run.run_id,
                step_id=step_id,
                definition=definition,
                arguments_hash=arguments_hash,
                evaluated_at=now,
            )
            frozen_steps.append(
                FrozenToolPlanStep(
                    step_id=step_id,
                    sequence_no=sequence_no,
                    tool_id=definition.tool_id,
                    tool_version=definition.tool_version,
                    canonical_arguments_hash=arguments_hash,
                    budget=_step_budget(run.budget, definition),
                    policy=policy,
                )
            )

        # 2. 所有候选均通过后才交给唯一 Store，Run、Step、预算和策略证据一起提交。
        return self._store.freeze_read_plan(
            workspace_id=run.workspace_id,
            run_id=run.run_id,
            steps=tuple(frozen_steps),
            frozen_at=now,
        )

    def _require_release(
        self,
        workspace_id: UUID,
        service_id: UUID,
        agent_release_id: UUID,
    ) -> ToolReleasePlan:
        release = self._releases.get_bound(workspace_id, service_id, agent_release_id)
        if release is None:
            raise ToolExecutionDeniedError
        return release


def parse_candidate_tool_intents(document: object) -> tuple[CandidateToolIntent, ...]:
    """严格解析模型工具调用信封，未知字段、宽松类型和超大参数全部拒绝。"""

    # 1. 先封闭根信封与调用数量，空计划和超长计划不能进入逐项解析。
    if not isinstance(document, Mapping) or set(document) != {"tool_calls"}:
        raise ToolResultRejectedError
    raw_calls = document.get("tool_calls")
    if not isinstance(raw_calls, list) or not 1 <= len(raw_calls) <= 50:
        raise ToolResultRejectedError
    # 2. 每项独立校验稳定身份与规范 JSON 大小，任何坏项都会拒绝整个候选集合。
    intents: list[CandidateToolIntent] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping) or set(raw_call) != {
            "tool_key",
            "tool_id",
            "tool_version",
            "arguments",
        }:
            raise ToolResultRejectedError
        tool_key = raw_call.get("tool_key")
        tool_id = raw_call.get("tool_id")
        tool_version = raw_call.get("tool_version")
        arguments = raw_call.get("arguments")
        if (
            not isinstance(tool_key, str)
            or TOOL_KEY_PATTERN.fullmatch(tool_key) is None
            or not isinstance(tool_id, str)
            or isinstance(tool_version, bool)
            or not isinstance(tool_version, int)
            or tool_version < 1
            or not isinstance(arguments, Mapping)
        ):
            raise ToolResultRejectedError
        try:
            parsed_tool_id = UUID(tool_id)
        except ValueError as error:
            raise ToolResultRejectedError from error
        normalized_arguments = dict(arguments)
        if len(_canonical_json(normalized_arguments)) > MAXIMUM_ARGUMENT_BYTES:
            raise ToolResultRejectedError
        intents.append(
            CandidateToolIntent(
                tool_key=tool_key,
                tool_id=parsed_tool_id,
                tool_version=tool_version,
                arguments=normalized_arguments,
            )
        )
    return tuple(intents)


def _release_allowlist(
    release: ToolReleasePlan,
) -> dict[tuple[UUID, int], ReleaseToolReference]:
    if release.tools and (
        release.release_snapshot_hash is None or len(release.release_snapshot_hash) != 64
    ):
        raise ToolExecutionDeniedError
    allowed = {(item.tool_id, item.tool_version): item for item in release.tools}
    if len(allowed) != len(release.tools):
        raise ToolExecutionDeniedError
    return allowed


def _target_resource_id(intent: CandidateToolIntent) -> UUID | None:
    argument_name = RESOURCE_ARGUMENTS.get(intent.tool_key)
    if argument_name is None:
        return None
    value = intent.arguments.get(argument_name)
    if not isinstance(value, str):
        raise ToolResultRejectedError
    try:
        return UUID(value)
    except ValueError as error:
        raise ToolResultRejectedError from error


def _require_release_match(
    intent: CandidateToolIntent,
    release_permission_code: str,
    definition: ToolDefinition,
) -> None:
    verify_tool_definition(definition)
    if (
        intent.tool_key != definition.tool_key
        or release_permission_code != definition.permission_code
        or definition.access_mode != "read"
        or definition.adapter_kind != "internal_read"
        or definition.credential_requirement != "none"
        or definition.synthetic
    ):
        raise ToolExecutionDeniedError


def _validate_and_hash_arguments(
    definition: ToolDefinition,
    arguments: Mapping[str, object],
) -> str:
    validator = Draft202012Validator(
        definition.input_schema_document,
        format_checker=FormatChecker(),
    )
    document = dict(arguments)
    if any(validator.iter_errors(document)):
        raise ToolResultRejectedError
    payload = _canonical_json(document)
    if len(payload) > MAXIMUM_ARGUMENT_BYTES:
        raise ToolResultRejectedError
    return hashlib.sha256(payload).hexdigest()


def _step_budget(run_budget: ToolRunBudget, definition: ToolDefinition) -> ToolStepBudget:
    """把工具限制与 Run 上限取交集，超时越界必须在进入 Store 前拒绝。"""

    if definition.timeout_seconds > run_budget.max_execution_seconds:
        raise ToolRunBudgetExceededError
    return ToolStepBudget(
        timeout_seconds=definition.timeout_seconds,
        max_attempts=(
            run_budget.max_attempts_per_step if definition.retry_mode == "safe_read" else 1
        ),
        max_result_bytes=MAXIMUM_RESULT_BYTES,
        max_cost_microunits=0,
    )


def build_tool_policy_decision_record(
    decision: PolicyDecision,
    *,
    workspace_id: UUID,
    run_id: UUID,
    step_id: UUID,
    definition: ToolDefinition,
    arguments_hash: str,
    evaluated_at: datetime,
) -> ToolPolicyDecisionRecord:
    """把当前 PDP 结果绑定到具体 Run、Step、工具版本和参数摘要。"""

    if (
        not decision.allowed
        or decision.workspace_id != workspace_id
        or decision.permission_code != definition.permission_code
        or decision.policy_version < 1
    ):
        raise ToolExecutionDeniedError
    scope = decision.resource_scope
    scope_document = {
        "workspace": scope.workspace,
        "department_ids": sorted(str(value) for value in scope.department_ids),
        "account_ids": sorted(str(value) for value in scope.account_ids),
        "resource_ids": sorted(str(value) for value in scope.resource_ids),
    }
    return ToolPolicyDecisionRecord(
        decision_id=decision.decision_id,
        workspace_id=workspace_id,
        run_id=run_id,
        step_id=step_id,
        tool_id=definition.tool_id,
        tool_version=definition.tool_version,
        canonical_arguments_hash=arguments_hash,
        permission_code=definition.permission_code,
        policy_version=decision.policy_version,
        resource_scope_hash=hashlib.sha256(_canonical_json(scope_document)).hexdigest(),
        field_mask_hash=hashlib.sha256(_canonical_json(sorted(decision.field_mask))).hexdigest(),
        evaluated_at=evaluated_at,
    )


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ToolResultRejectedError from error


__all__ = [
    "MAXIMUM_ARGUMENT_BYTES",
    "ToolExecutionPlanningService",
    "ToolPlanningCatalog",
    "build_tool_policy_decision_record",
    "parse_candidate_tool_intents",
]
