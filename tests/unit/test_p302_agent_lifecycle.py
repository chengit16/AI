"""验证 P3-02 Agent 草稿历史、候选冻结和幂等生命周期规则。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from uuid import UUID, uuid5

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_control.application.service import (
    AgentControlService,
    AgentIdempotencyConflictError,
    AgentLifecycleConflictError,
)
from ai_platform_api.modules.agent_control.domain.approval import AgentApprovalRepository
from ai_platform_api.modules.agent_control.domain.configuration import (
    AgentKnowledgeScopeVersion,
    AgentOutputSchemaVersion,
    AgentPromptVersion,
    AgentSafetyPolicyVersion,
    AgentToolDefinition,
    KnowledgeBaseReference,
    RuntimeConfigurationReference,
    WorkflowReleaseReference,
)
from ai_platform_api.modules.agent_control.domain.evaluation import (
    AgentEvaluationRepository,
)
from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlRequest,
    AgentDraft,
    AgentDraftRevision,
    AgentRelease,
    AgentReleaseCandidate,
    AgentRepository,
)
from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000302")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000302")
TRACE = TraceContext("3" * 32, "4" * 16)
RUNTIME_CONFIG_ID = UUID("a4000000-0000-4000-8000-000000000302")
SAFETY_POLICY_ID = UUID("a9000000-0000-4000-8000-000000000001")
VERSION_NAMESPACE = UUID("ac000000-0000-4000-8000-000000000302")
CREATED_AT = datetime(2026, 8, 16, tzinfo=UTC)


class MemoryWriter:
    """收集应用层写入的审计或 Outbox 事实。"""

    def __init__(self) -> None:
        self.records: list[AuditRecord | IntegrationEvent] = []

    def add(self, record: AuditRecord | IntegrationEvent) -> None:
        self.records.append(record)


class MemoryAgentRepository(AgentRepository):
    """用内存映射模拟工作空间隔离 Repository，供领域编排测试使用。"""

    def __init__(self) -> None:
        self.agents: dict[UUID, Agent] = {}
        self.drafts: dict[UUID, AgentDraft] = {}
        self.revisions: dict[tuple[UUID, int], AgentDraftRevision] = {}
        self.candidates: dict[UUID, AgentReleaseCandidate] = {}
        self.releases: dict[UUID, AgentRelease] = {}
        self.requests: dict[tuple[UUID, UUID, str, str], AgentControlRequest] = {}

    def add_agent(
        self,
        agent: Agent,
        draft: AgentDraft,
        revision: AgentDraftRevision,
    ) -> None:
        self.agents[agent.agent_id] = agent
        self.drafts[draft.agent_id] = draft
        self.revisions[(draft.draft_id, revision.revision)] = revision

    def get_agent(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        for_update: bool = False,
    ) -> Agent | None:
        del for_update
        agent = self.agents.get(agent_id)
        return agent if agent is not None and agent.workspace_id == workspace_id else None

    def save_agent(self, agent: Agent, *, expected_version: int) -> bool:
        current = self.agents.get(agent.agent_id)
        if current is None or current.version != expected_version:
            return False
        self.agents[agent.agent_id] = agent
        return True

    def get_draft(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentDraft | None:
        del for_update
        draft = self.drafts.get(agent_id)
        return draft if draft is not None and draft.workspace_id == workspace_id else None

    def save_draft(self, draft: AgentDraft, *, expected_revision: int) -> bool:
        current = self.drafts.get(draft.agent_id)
        if current is None or current.revision != expected_revision:
            return False
        self.drafts[draft.agent_id] = draft
        return True

    def add_draft_revision(self, revision: AgentDraftRevision) -> None:
        self.revisions[(revision.draft_id, revision.revision)] = revision

    def get_draft_revision(
        self,
        workspace_id: UUID,
        draft_id: UUID,
        revision: int,
    ) -> AgentDraftRevision | None:
        value = self.revisions.get((draft_id, revision))
        return value if value is not None and value.workspace_id == workspace_id else None

    def list_draft_revisions(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        limit: int,
    ) -> tuple[AgentDraftRevision, ...]:
        values = [
            value
            for value in self.revisions.values()
            if value.workspace_id == workspace_id and value.agent_id == agent_id
        ]
        return tuple(sorted(values, key=lambda item: item.revision, reverse=True)[:limit])

    def add_candidate(self, candidate: AgentReleaseCandidate) -> None:
        self.candidates[candidate.candidate_id] = candidate

    def get_candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentReleaseCandidate | None:
        del for_update
        value = self.candidates.get(candidate_id)
        return value if value is not None and value.workspace_id == workspace_id else None

    def save_candidate(
        self,
        candidate: AgentReleaseCandidate,
        *,
        expected_version: int,
    ) -> bool:
        current = self.candidates.get(candidate.candidate_id)
        if current is None or current.version != expected_version:
            return False
        self.candidates[candidate.candidate_id] = candidate
        return True

    def supersede_candidates_for_draft(
        self,
        workspace_id: UUID,
        draft_id: UUID,
        *,
        through_revision: int,
        updated_at: datetime,
    ) -> int:
        superseded = 0
        for candidate_id, candidate in tuple(self.candidates.items()):
            if (
                candidate.workspace_id == workspace_id
                and candidate.draft_id == draft_id
                and candidate.draft_revision <= through_revision
                and candidate.status
                in {
                    "ready_for_approval",
                    "approval_pending",
                    "approved",
                    "rejected",
                }
            ):
                self.candidates[candidate_id] = replace(
                    candidate,
                    status="superseded",
                    updated_at=updated_at,
                    version=candidate.version + 1,
                )
                superseded += 1
        return superseded

    def get_release(self, workspace_id: UUID, release_id: UUID) -> AgentRelease | None:
        value = self.releases.get(release_id)
        return value if value is not None and value.workspace_id == workspace_id else None

    def get_release_by_candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AgentRelease | None:
        return next(
            (
                release
                for release in self.releases.values()
                if release.workspace_id == workspace_id and release.candidate_id == candidate_id
            ),
            None,
        )

    def next_release_version(self, workspace_id: UUID, agent_id: UUID) -> int:
        versions = [
            release.version
            for release in self.releases.values()
            if release.workspace_id == workspace_id and release.agent_id == agent_id
        ]
        return max(versions, default=0) + 1

    def add_release(self, release: AgentRelease) -> None:
        self.releases[release.release_id] = release

    def get_request(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> AgentControlRequest | None:
        return self.requests.get((workspace_id, actor_id, operation, idempotency_key))

    def add_request(self, request: AgentControlRequest) -> None:
        self.requests[
            (request.workspace_id, request.actor_id, request.operation, request.idempotency_key)
        ] = request


class MemoryConfigurationRepository:
    """提供 P3-02 回归所需的最小已发布配置与不可变资源事实。"""

    def __init__(self) -> None:
        self.prompts: dict[UUID, AgentPromptVersion] = {}
        self.scopes: dict[UUID, AgentKnowledgeScopeVersion] = {}
        self.output_schemas: dict[UUID, AgentOutputSchemaVersion] = {}
        self.safety = AgentSafetyPolicyVersion(
            SAFETY_POLICY_ID,
            "rag-safety",
            1,
            "rag-safety-v2",
            "4b7a460fa0a2ce10283b7f5affe3e04bd69c0a9b40238669a7c266bb63f83738",
            "active",
        )

    def add_prompt_version(self, version: AgentPromptVersion) -> bool:
        if version.prompt_version_id in self.prompts:
            return False
        self.prompts[version.prompt_version_id] = version
        return True

    def get_prompt_version(
        self,
        workspace_id: UUID,
        prompt_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentPromptVersion | None:
        del for_share
        value = self.prompts.get(prompt_version_id)
        return value if value is not None and value.workspace_id == workspace_id else None

    def add_knowledge_scope_version(self, version: AgentKnowledgeScopeVersion) -> bool:
        if version.knowledge_scope_version_id in self.scopes:
            return False
        self.scopes[version.knowledge_scope_version_id] = version
        return True

    def get_knowledge_scope_version(
        self,
        workspace_id: UUID,
        knowledge_scope_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentKnowledgeScopeVersion | None:
        del for_share
        value = self.scopes.get(knowledge_scope_version_id)
        return value if value is not None and value.workspace_id == workspace_id else None

    def get_knowledge_bases(
        self,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        *,
        for_share: bool = False,
    ) -> tuple[KnowledgeBaseReference, ...]:
        del workspace_id, knowledge_base_ids, for_share
        return ()

    def add_output_schema_version(self, version: AgentOutputSchemaVersion) -> bool:
        if version.output_schema_version_id in self.output_schemas:
            return False
        self.output_schemas[version.output_schema_version_id] = version
        return True

    def get_output_schema_version(
        self,
        workspace_id: UUID,
        output_schema_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentOutputSchemaVersion | None:
        del for_share
        value = self.output_schemas.get(output_schema_version_id)
        return value if value is not None and value.workspace_id == workspace_id else None

    def get_safety_policy_version(
        self,
        safety_policy_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentSafetyPolicyVersion | None:
        del for_share
        return (
            self.safety
            if safety_policy_version_id == self.safety.safety_policy_version_id
            else None
        )

    def get_tool_definition(
        self,
        tool_id: UUID,
        tool_version: int,
        *,
        for_share: bool = False,
    ) -> AgentToolDefinition | None:
        del tool_id, tool_version, for_share
        return None

    def get_current_runtime_configuration(
        self,
        runtime_config_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> RuntimeConfigurationReference | None:
        del for_share
        if runtime_config_version_id != RUNTIME_CONFIG_ID:
            return None
        return RuntimeConfigurationReference(
            RUNTIME_CONFIG_ID,
            1,
            100_000,
            4096,
            120_000,
            1_000_000,
        )

    def get_current_workflow_release(
        self,
        workspace_id: UUID,
        workflow_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> WorkflowReleaseReference | None:
        del workspace_id, workflow_version_id, for_share
        return None

    def seed_configuration(self, prompt_text: str) -> dict[str, object]:
        """写入摘要一致的合成版本事实并返回严格草稿配置。"""

        prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()
        prompt_id = uuid5(VERSION_NAMESPACE, f"prompt:{prompt_hash}")
        schema_document: dict[str, object] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string"}},
        }
        schema_hash = hashlib.sha256(
            b'{"$schema":"https://json-schema.org/draft/2020-12/schema","additionalProperties":false,"properties":{"answer":{"type":"string"}},"type":"object"}'
        ).hexdigest()
        output_id = uuid5(VERSION_NAMESPACE, f"output:{schema_hash}")
        scope_hash = hashlib.sha256(b"[]").hexdigest()
        scope_id = uuid5(VERSION_NAMESPACE, f"scope:{scope_hash}")
        self.prompts[prompt_id] = AgentPromptVersion(
            prompt_id,
            WORKSPACE_ID,
            "合成 Prompt",
            prompt_text,
            prompt_hash,
            ACCOUNT_ID,
            CREATED_AT,
        )
        self.output_schemas[output_id] = AgentOutputSchemaVersion(
            output_id,
            WORKSPACE_ID,
            "合成输出",
            schema_document,
            schema_hash,
            ACCOUNT_ID,
            CREATED_AT,
        )
        self.scopes[scope_id] = AgentKnowledgeScopeVersion(
            scope_id,
            WORKSPACE_ID,
            "空知识范围",
            (),
            scope_hash,
            ACCOUNT_ID,
            CREATED_AT,
        )
        return {
            "prompt_version_id": str(prompt_id),
            "runtime_config_version_id": str(RUNTIME_CONFIG_ID),
            "knowledge_scope_version_ids": [str(scope_id)],
            "workflow_release_id": None,
            "read_only_tools": [],
            "output_schema_version_id": str(output_id),
            "safety_policy_version_id": str(SAFETY_POLICY_ID),
            "limits": {
                "max_input_tokens": 8192,
                "max_output_tokens": 2048,
                "max_execution_seconds": 60,
                "max_cost_microunits": 500_000,
            },
        }


class MemoryAgentUnitOfWork:
    """模拟不嵌套事务，并记录应用服务显式提交次数。"""

    def __init__(self) -> None:
        self.agents = MemoryAgentRepository()
        self.configuration = MemoryConfigurationRepository()
        # P3-02 用例不调用评估端口，仅声明结构类型以保持统一 Unit of Work 契约。
        self.evaluation = cast(AgentEvaluationRepository, object())
        self.approval = cast(AgentApprovalRepository, object())
        self.audit = MemoryWriter()
        self.outbox = MemoryWriter()
        self.commit_count = 0

    def __enter__(self) -> MemoryAgentUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def commit(self) -> None:
        self.commit_count += 1


def context(*, workspace_id: UUID = WORKSPACE_ID) -> RequestContext:
    """构造通过工作空间授权的合成浏览器上下文。"""

    return replace(
        RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=workspace_id,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_workspace=True,
    )


def configuration(unit_of_work: MemoryAgentUnitOfWork, prompt_text: str) -> dict[str, object]:
    """构造由真实内存版本事实支持的严格合成草稿配置。"""

    return unit_of_work.configuration.seed_configuration(prompt_text)


def test_draft_updates_keep_history_and_reject_stale_revision() -> None:
    unit_of_work = MemoryAgentUnitOfWork()
    service = AgentControlService(unit_of_work)
    agent, first = service.create_agent(
        context(),
        name=" 合成知识 Agent ",
        description="P3-02 生命周期测试",
        configuration=configuration(unit_of_work, "合成 Prompt v1"),
        idempotency_key="synthetic-agent-create-0302",
    )

    updated = service.update_draft(
        context(),
        agent_id=agent.agent_id,
        expected_revision=first.revision,
        configuration=configuration(unit_of_work, "合成 Prompt v2"),
        idempotency_key="synthetic-agent-update-0302",
    )

    assert updated.revision == 2
    assert updated.config_hash != first.config_hash
    assert [
        item.revision
        for item in service.list_draft_revisions(context(), agent_id=agent.agent_id, limit=20)
    ] == [2, 1]
    with pytest.raises(AgentLifecycleConflictError):
        service.update_draft(
            context(),
            agent_id=agent.agent_id,
            expected_revision=first.revision,
            configuration=configuration(unit_of_work, "合成 Prompt v3"),
            idempotency_key="synthetic-agent-stale-0302",
        )


def test_idempotent_writes_do_not_duplicate_facts_and_conflicting_payload_is_rejected() -> None:
    unit_of_work = MemoryAgentUnitOfWork()
    service = AgentControlService(unit_of_work)
    first = service.create_agent(
        context(),
        name="合成幂等 Agent",
        description=None,
        configuration=configuration(unit_of_work, "合成幂等 Prompt"),
        idempotency_key="synthetic-agent-idempotent-0302",
    )
    repeated = service.create_agent(
        context(),
        name="合成幂等 Agent",
        description=None,
        configuration=configuration(unit_of_work, "合成幂等 Prompt"),
        idempotency_key="synthetic-agent-idempotent-0302",
    )

    assert repeated == first
    assert len(unit_of_work.agents.agents) == 1
    assert len(unit_of_work.audit.records) == 1
    assert len(unit_of_work.outbox.records) == 1
    with pytest.raises(AgentIdempotencyConflictError):
        service.create_agent(
            context(),
            name="不同的合成 Agent",
            description=None,
            configuration=configuration(unit_of_work, "合成幂等 Prompt"),
            idempotency_key="synthetic-agent-idempotent-0302",
        )


def test_candidate_freezes_revision_and_archiving_supersedes_current_draft() -> None:
    unit_of_work = MemoryAgentUnitOfWork()
    service = AgentControlService(unit_of_work)
    agent, draft = service.create_agent(
        context(),
        name="合成候选 Agent",
        description=None,
        configuration=configuration(unit_of_work, "合成候选 Prompt"),
        idempotency_key="synthetic-agent-candidate-create-0302",
    )
    candidate = service.request_release_candidate(
        context(),
        agent_id=agent.agent_id,
        expected_revision=draft.revision,
        idempotency_key="synthetic-agent-candidate-0302",
    )

    assert candidate.draft_id == draft.draft_id
    assert candidate.draft_revision == draft.revision
    assert candidate.config_hash == draft.config_hash
    assert candidate.status == "created"

    archived = service.archive_agent(
        context(),
        agent_id=agent.agent_id,
        expected_version=agent.version,
        idempotency_key="synthetic-agent-archive-0302",
    )
    assert archived.status == "archived"
    assert unit_of_work.agents.drafts[agent.agent_id].status == "superseded"
    with pytest.raises(AgentLifecycleConflictError):
        service.request_release_candidate(
            context(),
            agent_id=agent.agent_id,
            expected_revision=draft.revision,
            idempotency_key="synthetic-agent-candidate-after-archive-0302",
        )
