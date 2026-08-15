"""定义 Agent、草稿修订、发布候选和不可变 Release 生命周期事实。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter

from ai_platform_api.modules.agent_control.domain.configuration import (
    AgentConfigurationRepository,
)
from ai_platform_api.modules.agent_control.domain.evaluation import (
    AgentEvaluationRepository,
)
from ai_platform_api.modules.integration.domain.events import OutboxWriter

AgentKind = Literal["system", "custom"]
AgentStatus = Literal["active", "archived"]
AgentDraftStatus = Literal[
    "editing",
    "testing",
    "test_failed",
    "ready_for_approval",
    "approval_pending",
    "approved",
    "rejected",
    "superseded",
]
AgentReleaseCandidateStatus = Literal[
    "created",
    "testing",
    "test_failed",
    "ready_for_approval",
    "approval_pending",
    "approved",
    "rejected",
    "released",
    "superseded",
]
AgentReleaseKind = Literal["system", "custom"]
AgentControlResultType = Literal["agent", "draft", "candidate"]


@dataclass(frozen=True)
class Agent:
    """表示工作空间内的 Agent 定义；系统与自定义写入权由 kind 隔离。"""

    agent_id: UUID
    workspace_id: UUID
    agent_key: str
    agent_kind: AgentKind
    name: str
    description: str | None
    status: AgentStatus
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class AgentDraft:
    """保存 Agent 当前可修改配置和乐观锁 revision，不作为 Runtime 输入。"""

    draft_id: UUID
    agent_id: UUID
    workspace_id: UUID
    revision: int
    status: AgentDraftStatus
    configuration: dict[str, object]
    config_hash: str
    updated_by_account_id: UUID
    updated_at: datetime


@dataclass(frozen=True)
class AgentDraftRevision:
    """冻结每次草稿写入后的完整修订，历史记录只能插入和读取。"""

    draft_id: UUID
    agent_id: UUID
    workspace_id: UUID
    revision: int
    status: AgentDraftStatus
    configuration: dict[str, object]
    config_hash: str
    updated_by_account_id: UUID
    updated_at: datetime


@dataclass(frozen=True)
class AgentReleaseCandidate:
    """冻结申请发布时的草稿 revision 与配置摘要，后续只能推进候选状态。"""

    candidate_id: UUID
    agent_id: UUID
    draft_id: UUID
    workspace_id: UUID
    draft_revision: int
    candidate_hash: str
    config_hash: str
    status: AgentReleaseCandidateStatus
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class AgentRelease:
    """映射系统或自定义 Agent 的共享不可变发布事实。"""

    release_id: UUID
    agent_id: UUID
    workspace_id: UUID
    release_kind: AgentReleaseKind
    version: int
    runtime_config_version_id: UUID
    config_hash: str
    candidate_id: UUID | None
    candidate_hash: str | None
    snapshot: dict[str, object] | None
    snapshot_hash: str | None
    released_by_account_id: UUID
    released_at: datetime


@dataclass(frozen=True)
class AgentControlRequest:
    """冻结高风险写请求与结果身份，使重试不会重复产生业务事实。"""

    request_id: UUID
    workspace_id: UUID
    actor_id: UUID
    operation: str
    idempotency_key: str
    request_hash: str
    result_type: AgentControlResultType
    result_id: UUID
    result_revision: int | None
    created_at: datetime


class AgentWriteConflictError(Exception):
    """数据库唯一约束、乐观锁或候选来源竞争拒绝写入。"""

    def __init__(
        self,
        reason: Literal["revision", "version", "idempotency", "candidate_source", "write"],
    ) -> None:
        self.reason = reason
        super().__init__(reason)


class AgentRepository(Protocol):
    """按工作空间边界维护 Agent 当前事实、不可变历史和幂等请求。"""

    def add_agent(
        self,
        agent: Agent,
        draft: AgentDraft,
        revision: AgentDraftRevision,
    ) -> None: ...

    def get_agent(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        for_update: bool = False,
    ) -> Agent | None: ...

    def save_agent(self, agent: Agent, *, expected_version: int) -> bool: ...

    def get_draft(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentDraft | None: ...

    def save_draft(self, draft: AgentDraft, *, expected_revision: int) -> bool: ...

    def add_draft_revision(self, revision: AgentDraftRevision) -> None: ...

    def get_draft_revision(
        self,
        workspace_id: UUID,
        draft_id: UUID,
        revision: int,
    ) -> AgentDraftRevision | None: ...

    def list_draft_revisions(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        limit: int,
    ) -> tuple[AgentDraftRevision, ...]: ...

    def add_candidate(self, candidate: AgentReleaseCandidate) -> None: ...

    def get_candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AgentReleaseCandidate | None: ...

    def get_release(self, workspace_id: UUID, release_id: UUID) -> AgentRelease | None: ...

    def get_request(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> AgentControlRequest | None: ...

    def add_request(self, request: AgentControlRequest) -> None: ...


class AgentControlUnitOfWork(Protocol):
    """保证 Agent 业务事实、幂等、审计和 Outbox 在同一事务提交。"""

    @property
    def agents(self) -> AgentRepository: ...

    @property
    def configuration(self) -> AgentConfigurationRepository: ...

    @property
    def evaluation(self) -> AgentEvaluationRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> AgentControlUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
