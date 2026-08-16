"""验证 P4-05 候选意图只能冻结为 Release 与当前策略共同允许的计划。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.policy import (
    AuthorizationPolicyUnavailableError,
)
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    ResourceScope,
)
from ai_platform_api.modules.tool_execution.application.definitions import (
    parse_tool_definition,
)
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolExecutionDeniedError,
    ToolResultRejectedError,
    ToolRunBudgetExceededError,
)
from ai_platform_api.modules.tool_execution.application.planning import (
    ToolExecutionPlanningService,
    parse_candidate_tool_intents,
)
from ai_platform_api.modules.tool_execution.domain.catalog import ToolDefinition
from ai_platform_api.modules.tool_execution.domain.planning import (
    CandidateToolIntent,
    FrozenToolPlanStep,
    ReleaseToolReference,
    ToolExecutionPlan,
    ToolReleasePlan,
)
from ai_platform_api.modules.tool_execution.domain.tasks import (
    ToolRun,
    ToolRunBudget,
    ToolStep,
)

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("a9000000-0000-4000-8000-000000000001")
ACCOUNT_ID = UUID("a9000000-0000-4000-8000-000000000002")
SERVICE_ID = UUID("a9000000-0000-4000-8000-000000000003")
RELEASE_ID = UUID("a9000000-0000-4000-8000-000000000004")
RUN_ID = UUID("a9000000-0000-4000-8000-000000000005")
TOOL_ID = UUID("a7000000-0000-4000-8000-000000000001")
RESOURCE_ID = UUID("a9000000-0000-4000-8000-000000000006")
TRACE = TraceContext("9" * 32, "a" * 16)


class StubTasks:
    """只返回已冻结 Run，计划服务不能通过任务接口追加 Step。"""

    def __init__(self, run: ToolRun) -> None:
        self.run = run

    def get_run(self, context: RequestContext, run_id: UUID) -> ToolRun:
        assert context.workspace_id == self.run.workspace_id and run_id == self.run.run_id
        return self.run


class RecordingPlanStore:
    """记录唯一写命令；单元测试不复制 PostgreSQL 的事务实现。"""

    def __init__(self, run: ToolRun) -> None:
        self.run = run
        self.calls: list[tuple[FrozenToolPlanStep, ...]] = []

    def get_run(self, workspace_id: UUID, run_id: UUID) -> ToolRun | None:
        if (workspace_id, run_id) != (self.run.workspace_id, self.run.run_id):
            return None
        return self.run

    def freeze_read_plan(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        steps: tuple[FrozenToolPlanStep, ...],
        frozen_at: datetime,
    ) -> ToolExecutionPlan:
        self.calls.append(steps)
        persisted = tuple(
            ToolStep(
                step_id=item.step_id,
                run_id=run_id,
                workspace_id=workspace_id,
                sequence_no=item.sequence_no,
                tool_id=item.tool_id,
                tool_version=item.tool_version,
                canonical_arguments_hash=item.canonical_arguments_hash,
                budget=item.budget,
                state="ready",
                current_attempt_no=None,
                created_at=frozen_at,
                updated_at=frozen_at,
                version=3,
            )
            for item in steps
        )
        return ToolExecutionPlan(
            run=replace(self.run, state="running", updated_at=frozen_at, version=3),
            steps=persisted,
            policies=tuple(item.policy for item in steps),
        )


class StubCatalog:
    """返回当前 PDP 证据，并允许测试撤权与策略不可用分支。"""

    def __init__(self, definition: ToolDefinition) -> None:
        self.definition = definition
        self.resource_ids: list[UUID | None] = []
        self.failure: Exception | None = None

    def authorize_available_tool(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        tool_id: UUID,
        tool_version: int,
        resource_id: UUID | None = None,
    ) -> tuple[ToolDefinition, PolicyDecision]:
        if self.failure is not None:
            raise self.failure
        assert context.workspace_id == workspace_id
        assert (tool_id, tool_version) == (
            self.definition.tool_id,
            self.definition.tool_version,
        )
        self.resource_ids.append(resource_id)
        return self.definition, _decision(self.definition.permission_code)


class StubReleases:
    """返回 Run 精确绑定的不可变 Release 允许列表。"""

    def __init__(self, release: ToolReleasePlan | None) -> None:
        self.release = release

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        agent_release_id: UUID,
    ) -> ToolReleasePlan | None:
        if self.release is None:
            return None
        assert (workspace_id, service_id, agent_release_id) == (
            self.release.workspace_id,
            self.release.service_id,
            self.release.agent_release_id,
        )
        return self.release


def _context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session",
    )


def _run() -> ToolRun:
    return ToolRun(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        requested_by_actor_id=ACCOUNT_ID,
        requested_by_account_id=ACCOUNT_ID,
        service_id=SERVICE_ID,
        agent_release_id=RELEASE_ID,
        state="pending",
        budget=ToolRunBudget(2, 2, 120, 0),
        cancel_requested_at=None,
        deadline_at=NOW + timedelta(seconds=120),
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
        version=1,
    )


def _definition(*, timeout_seconds: int = 15) -> ToolDefinition:
    object_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["document_id"],
        "properties": {
            "document_id": {"type": "string", "format": "uuid"},
            "start": {"type": "integer", "minimum": 0},
        },
    }
    return parse_tool_definition(
        {
            "tool_id": str(TOOL_ID),
            "tool_version": 1,
            "tool_key": "document.read_authorized_range",
            "display_name": "合成文档范围读取",
            "description": "仅用于 P4-05 计划测试。",
            "access_mode": "read",
            "risk_level": "medium",
            "adapter_kind": "internal_read",
            "input_schema_document": object_schema,
            "output_schema_document": object_schema,
            "permission_code": "knowledge.document.read",
            "credential_requirement": "none",
            "timeout_seconds": timeout_seconds,
            "retry_mode": "safe_read",
            "status": "active",
            "synthetic": False,
        },
        created_at=NOW,
    )


def _release(definition: ToolDefinition) -> ToolReleasePlan:
    return ToolReleasePlan(
        workspace_id=WORKSPACE_ID,
        service_id=SERVICE_ID,
        agent_release_id=RELEASE_ID,
        release_snapshot_hash="b" * 64,
        tools=(
            ReleaseToolReference(
                definition.tool_id,
                definition.tool_version,
                "read",
                definition.permission_code,
            ),
        ),
    )


def _decision(permission_code: str) -> PolicyDecision:
    return PolicyDecision(
        decision_id=UUID("a9000000-0000-4000-8000-000000000007"),
        decision="allow",
        permission_code=permission_code,
        workspace_id=WORKSPACE_ID,
        resource_scope=ResourceScope(resource_ids=frozenset({RESOURCE_ID})),
        field_mask=frozenset({"secret"}),
        policy_version=3,
        cache_ttl_seconds=0,
        reason="role_permission_granted",
    )


def _intent(definition: ToolDefinition) -> CandidateToolIntent:
    return CandidateToolIntent(
        tool_key=definition.tool_key,
        tool_id=definition.tool_id,
        tool_version=definition.tool_version,
        arguments={"document_id": str(RESOURCE_ID), "start": 0},
    )


def _service() -> tuple[
    ToolExecutionPlanningService,
    RecordingPlanStore,
    StubCatalog,
    StubTasks,
    StubReleases,
]:
    run = _run()
    definition = _definition()
    store = RecordingPlanStore(run)
    catalog = StubCatalog(definition)
    tasks = StubTasks(run)
    releases = StubReleases(_release(definition))
    service = ToolExecutionPlanningService(tasks, store, catalog, releases)
    return service, store, catalog, tasks, releases


def test_strict_candidate_envelope_rejects_unknown_loose_and_oversized_values() -> None:
    valid_call: dict[str, object] = {
        "tool_key": "document.read_authorized_range",
        "tool_id": str(TOOL_ID),
        "tool_version": 1,
        "arguments": {"document_id": str(RESOURCE_ID)},
    }
    valid: dict[str, object] = {"tool_calls": [valid_call]}
    assert parse_candidate_tool_intents(valid)[0].tool_id == TOOL_ID

    invalid_documents: tuple[object, ...] = (
        {**valid, "unknown": True},
        {"tool_calls": [{**valid_call, "tool_version": True}]},
        {"tool_calls": [{**valid_call, "tool_id": "not-a-uuid"}]},
        {"tool_calls": [{**valid_call, "arguments": {"value": "x" * 65_536}}]},
    )
    for document in invalid_documents:
        with pytest.raises(ToolResultRejectedError):
            parse_candidate_tool_intents(document)


def test_plan_freezes_exact_release_schema_budget_resource_policy_and_hashes() -> None:
    service, store, catalog, _, _ = _service()
    definition = catalog.definition

    result = service.freeze(_context(), RUN_ID, (_intent(definition),), evaluated_at=NOW)

    assert result.run.state == "running"
    assert len(store.calls) == 1 and len(result.steps) == 1
    assert catalog.resource_ids == [RESOURCE_ID]
    assert result.steps[0].budget.timeout_seconds == definition.timeout_seconds
    assert result.steps[0].budget.max_attempts == 2
    assert result.steps[0].budget.max_result_bytes == 262_144
    assert len(result.steps[0].canonical_arguments_hash) == 64
    assert result.policies[0].policy_version == 3
    assert len(result.policies[0].resource_scope_hash) == 64
    assert len(result.policies[0].field_mask_hash) == 64


@pytest.mark.parametrize("failure", ["release", "schema", "revoked", "unavailable"])
def test_full_preflight_failure_never_writes_plan(failure: str) -> None:
    service, store, catalog, _, releases = _service()
    definition = catalog.definition
    intent = _intent(definition)
    expected_error: type[Exception]
    if failure == "release":
        releases.release = replace(_release(definition), tools=())
        expected_error = ToolExecutionDeniedError
    elif failure == "schema":
        intent = replace(intent, arguments={"document_id": "not-a-uuid"})
        expected_error = ToolResultRejectedError
    elif failure == "revoked":
        catalog.failure = ToolExecutionDeniedError()
        expected_error = ToolExecutionDeniedError
    else:
        catalog.failure = AuthorizationPolicyUnavailableError()
        expected_error = AuthorizationPolicyUnavailableError

    with pytest.raises(expected_error):
        service.freeze(_context(), RUN_ID, (intent,), evaluated_at=NOW)
    assert store.calls == []


def test_deadline_and_step_budget_are_checked_before_catalog_or_store() -> None:
    service, store, catalog, tasks, _ = _service()
    tasks.run = replace(_run(), deadline_at=NOW)

    with pytest.raises(ToolRunBudgetExceededError):
        service.freeze(_context(), RUN_ID, (_intent(catalog.definition),), evaluated_at=NOW)

    assert catalog.resource_ids == []
    assert store.calls == []

    service, store, catalog, tasks, _ = _service()
    tasks.run = replace(
        tasks.run,
        budget=replace(tasks.run.budget, max_execution_seconds=60),
    )
    catalog.definition = _definition(timeout_seconds=120)
    with pytest.raises(ToolRunBudgetExceededError):
        service.freeze(_context(), RUN_ID, (_intent(catalog.definition),), evaluated_at=NOW)
    assert store.calls == []
