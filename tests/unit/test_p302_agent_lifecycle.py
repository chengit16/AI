"""验证 P3-02 Agent 草稿历史、候选冻结和幂等生命周期规则。"""

from __future__ import annotations

from dataclasses import replace
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_control.application.service import (
    AgentControlService,
    AgentIdempotencyConflictError,
    AgentLifecycleConflictError,
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
    ) -> AgentReleaseCandidate | None:
        value = self.candidates.get(candidate_id)
        return value if value is not None and value.workspace_id == workspace_id else None

    def get_release(self, workspace_id: UUID, release_id: UUID) -> AgentRelease | None:
        value = self.releases.get(release_id)
        return value if value is not None and value.workspace_id == workspace_id else None

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


class MemoryAgentUnitOfWork:
    """模拟不嵌套事务，并记录应用服务显式提交次数。"""

    def __init__(self) -> None:
        self.agents = MemoryAgentRepository()
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


def configuration(prompt_version: str) -> dict[str, object]:
    """构造只含合成引用的最小草稿配置；语义校验由 P3-03 继续收紧。"""

    return {
        "schema_version": 1,
        "prompt_version": prompt_version,
        "runtime_config_version_id": "a4000000-0000-4000-8000-000000000302",
        "read_only_tools": [],
    }


def test_draft_updates_keep_history_and_reject_stale_revision() -> None:
    unit_of_work = MemoryAgentUnitOfWork()
    service = AgentControlService(unit_of_work)
    agent, first = service.create_agent(
        context(),
        name=" 合成知识 Agent ",
        description="P3-02 生命周期测试",
        configuration=configuration("v1"),
        idempotency_key="synthetic-agent-create-0302",
    )

    updated = service.update_draft(
        context(),
        agent_id=agent.agent_id,
        expected_revision=first.revision,
        configuration=configuration("v2"),
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
            configuration=configuration("v3"),
            idempotency_key="synthetic-agent-stale-0302",
        )


def test_idempotent_writes_do_not_duplicate_facts_and_conflicting_payload_is_rejected() -> None:
    unit_of_work = MemoryAgentUnitOfWork()
    service = AgentControlService(unit_of_work)
    first = service.create_agent(
        context(),
        name="合成幂等 Agent",
        description=None,
        configuration=configuration("v1"),
        idempotency_key="synthetic-agent-idempotent-0302",
    )
    repeated = service.create_agent(
        context(),
        name="合成幂等 Agent",
        description=None,
        configuration=configuration("v1"),
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
            configuration=configuration("v1"),
            idempotency_key="synthetic-agent-idempotent-0302",
        )


def test_candidate_freezes_revision_and_archiving_supersedes_current_draft() -> None:
    unit_of_work = MemoryAgentUnitOfWork()
    service = AgentControlService(unit_of_work)
    agent, draft = service.create_agent(
        context(),
        name="合成候选 Agent",
        description=None,
        configuration=configuration("v1"),
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
