"""实现 Agent 生命周期、不可变历史、候选和幂等事实的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlRequest,
    AgentControlResultType,
    AgentControlUnitOfWork,
    AgentDraft,
    AgentDraftRevision,
    AgentDraftStatus,
    AgentKind,
    AgentRelease,
    AgentReleaseCandidate,
    AgentReleaseCandidateStatus,
    AgentReleaseKind,
    AgentRepository,
    AgentStatus,
    AgentWriteConflictError,
)
from ai_platform_api.persistence.tables import (
    agent_control_requests,
    agent_draft_revisions,
    agent_drafts,
    agent_release_candidates,
    agent_releases,
    agents,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyAgentRepository(AgentRepository):
    """维护自定义 Agent 当前事实，并对历史 revision 和请求事实只执行插入。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_agent(
        self,
        agent: Agent,
        draft: AgentDraft,
        revision: AgentDraftRevision,
    ) -> None:
        """一次写入 Agent、当前草稿和首个历史 revision。"""

        try:
            self._session.execute(
                insert(agents).values(
                    agent_id=agent.agent_id,
                    workspace_id=agent.workspace_id,
                    agent_key=agent.agent_key,
                    agent_kind=agent.agent_kind,
                    name=agent.name,
                    description=agent.description,
                    status=agent.status,
                    created_by_account_id=agent.created_by_account_id,
                    created_at=agent.created_at,
                    updated_at=agent.updated_at,
                    version=agent.version,
                )
            )
            self._session.execute(insert(agent_drafts).values(**_draft_values(draft)))
            self._session.execute(
                insert(agent_draft_revisions).values(**_revision_values(revision))
            )
        except IntegrityError as error:
            raise AgentWriteConflictError("write") from error

    def get_agent(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        for_update: bool = False,
    ) -> Agent | None:
        """按工作空间和主键读取 Agent，可选获取定义行锁。"""

        statement = select(agents).where(
            agents.c.workspace_id == workspace_id,
            agents.c.agent_id == agent_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _agent(row) if row is not None else None

    def save_agent(self, agent: Agent, *, expected_version: int) -> bool:
        """用 version 条件更新定义，防止归档与其他写操作丢失更新。"""

        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(agents)
                .where(
                    agents.c.workspace_id == agent.workspace_id,
                    agents.c.agent_id == agent.agent_id,
                    agents.c.agent_kind == "custom",
                    agents.c.version == expected_version,
                )
                .values(
                    name=agent.name,
                    description=agent.description,
                    status=agent.status,
                    updated_at=agent.updated_at,
                    version=agent.version,
                )
            ),
        )
        return result.rowcount == 1

    def get_draft(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentDraft | None:
        """读取 Agent 唯一当前草稿，可选获取 revision 行锁。"""

        statement = select(agent_drafts).where(
            agent_drafts.c.workspace_id == workspace_id,
            agent_drafts.c.agent_id == agent_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _draft(row) if row is not None else None

    def save_draft(self, draft: AgentDraft, *, expected_revision: int) -> bool:
        """按 workspace、Agent 和 revision 原子替换当前草稿。"""

        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(agent_drafts)
                .where(
                    agent_drafts.c.workspace_id == draft.workspace_id,
                    agent_drafts.c.agent_id == draft.agent_id,
                    agent_drafts.c.draft_id == draft.draft_id,
                    agent_drafts.c.revision == expected_revision,
                )
                .values(
                    revision=draft.revision,
                    status=draft.status,
                    configuration=draft.configuration,
                    config_hash=draft.config_hash,
                    updated_by_account_id=draft.updated_by_account_id,
                    updated_at=draft.updated_at,
                )
            ),
        )
        return result.rowcount == 1

    def add_draft_revision(self, revision: AgentDraftRevision) -> None:
        """追加不可变草稿历史，相同 revision 竞争映射为领域冲突。"""

        try:
            self._session.execute(
                insert(agent_draft_revisions).values(**_revision_values(revision))
            )
        except IntegrityError as error:
            raise AgentWriteConflictError("revision") from error

    def get_draft_revision(
        self,
        workspace_id: UUID,
        draft_id: UUID,
        revision: int,
    ) -> AgentDraftRevision | None:
        """按工作空间、草稿和 revision 读取单个历史修订。"""

        row = self._session.execute(
            select(agent_draft_revisions).where(
                agent_draft_revisions.c.workspace_id == workspace_id,
                agent_draft_revisions.c.draft_id == draft_id,
                agent_draft_revisions.c.revision == revision,
            )
        ).one_or_none()
        return _revision(row) if row is not None else None

    def list_draft_revisions(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        limit: int,
    ) -> tuple[AgentDraftRevision, ...]:
        """按 revision 倒序读取一个 Agent 的有限历史。"""

        rows = self._session.execute(
            select(agent_draft_revisions)
            .where(
                agent_draft_revisions.c.workspace_id == workspace_id,
                agent_draft_revisions.c.agent_id == agent_id,
            )
            .order_by(agent_draft_revisions.c.revision.desc())
            .limit(limit)
        )
        return tuple(_revision(row) for row in rows)

    def add_candidate(self, candidate: AgentReleaseCandidate) -> None:
        """插入来源不可变的发布候选，同一 revision 只允许一个候选。"""

        try:
            self._session.execute(
                insert(agent_release_candidates).values(
                    candidate_id=candidate.candidate_id,
                    agent_id=candidate.agent_id,
                    draft_id=candidate.draft_id,
                    workspace_id=candidate.workspace_id,
                    draft_revision=candidate.draft_revision,
                    candidate_hash=candidate.candidate_hash,
                    config_hash=candidate.config_hash,
                    status=candidate.status,
                    created_by_account_id=candidate.created_by_account_id,
                    created_at=candidate.created_at,
                    updated_at=candidate.updated_at,
                    version=candidate.version,
                )
            )
        except IntegrityError as error:
            reason: Literal["candidate_source", "write"] = (
                "candidate_source"
                if _constraint_name(error) == "uq_agent_release_candidates_draft_revision"
                else "write"
            )
            raise AgentWriteConflictError(reason) from error

    def get_candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AgentReleaseCandidate | None:
        """读取工作空间内的单个发布候选。"""

        row = self._session.execute(
            select(agent_release_candidates).where(
                agent_release_candidates.c.workspace_id == workspace_id,
                agent_release_candidates.c.candidate_id == candidate_id,
            )
        ).one_or_none()
        return _candidate(row) if row is not None else None

    def get_release(self, workspace_id: UUID, release_id: UUID) -> AgentRelease | None:
        """读取共享表中的不可变 Release，不跟随当前发布指针。"""

        row = self._session.execute(
            select(agent_releases).where(
                agent_releases.c.workspace_id == workspace_id,
                agent_releases.c.release_id == release_id,
            )
        ).one_or_none()
        return _release(row) if row is not None else None

    def get_request(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        operation: str,
        idempotency_key: str,
    ) -> AgentControlRequest | None:
        """按可信主体和操作读取不可变幂等事实。"""

        row = self._session.execute(
            select(agent_control_requests).where(
                agent_control_requests.c.workspace_id == workspace_id,
                agent_control_requests.c.actor_id == actor_id,
                agent_control_requests.c.operation == operation,
                agent_control_requests.c.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        return _request(row) if row is not None else None

    def add_request(self, request: AgentControlRequest) -> None:
        """把幂等结果与业务写入放在同一事务，唯一键处理并发重试。"""

        try:
            self._session.execute(
                insert(agent_control_requests).values(
                    request_id=request.request_id,
                    workspace_id=request.workspace_id,
                    actor_id=request.actor_id,
                    operation=request.operation,
                    idempotency_key=request.idempotency_key,
                    request_hash=request.request_hash,
                    result_type=request.result_type,
                    result_id=request.result_id,
                    result_revision=request.result_revision,
                    created_at=request.created_at,
                )
            )
        except IntegrityError as error:
            reason: Literal["idempotency", "write"] = (
                "idempotency"
                if _constraint_name(error) == "uq_agent_control_requests_idempotency"
                else "write"
            )
            raise AgentWriteConflictError(reason) from error


class SqlAlchemyAgentControlUnitOfWork(AgentControlUnitOfWork):
    """为 Agent 控制面提供不可嵌套的显式 SQLAlchemy 事务边界。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyAgentRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("agent_control_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyAgentControlUnitOfWork:
        """创建一次短事务所需的 Repository、审计和 Outbox Writer。"""

        if self._state.get() is not None:
            raise RuntimeError("Agent Control Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyAgentRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """异常时回滚并始终关闭 Session，防止失败事务被后续请求复用。"""

        del exc_value, traceback
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def agents(self) -> SqlAlchemyAgentRepository:
        return self._require_state()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._require_state()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._require_state()[3]

    def commit(self) -> None:
        """提交 Agent 业务事实、幂等、审计和 Outbox 的同一事务。"""

        self._require_state()[0].commit()

    def _require_state(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyAgentRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Agent Control Unit of Work 尚未进入事务范围")
        return state


def _draft_values(draft: AgentDraft) -> dict[str, object]:
    return {
        "draft_id": draft.draft_id,
        "agent_id": draft.agent_id,
        "workspace_id": draft.workspace_id,
        "revision": draft.revision,
        "status": draft.status,
        "configuration": draft.configuration,
        "config_hash": draft.config_hash,
        "updated_by_account_id": draft.updated_by_account_id,
        "updated_at": draft.updated_at,
    }


def _revision_values(revision: AgentDraftRevision) -> dict[str, object]:
    return {
        "draft_id": revision.draft_id,
        "agent_id": revision.agent_id,
        "workspace_id": revision.workspace_id,
        "revision": revision.revision,
        "status": revision.status,
        "configuration": revision.configuration,
        "config_hash": revision.config_hash,
        "updated_by_account_id": revision.updated_by_account_id,
        "updated_at": revision.updated_at,
    }


def _agent(row: Row[Any]) -> Agent:
    return Agent(
        agent_id=row.agent_id,
        workspace_id=row.workspace_id,
        agent_key=row.agent_key,
        agent_kind=cast("AgentKind", row.agent_kind),
        name=row.name,
        description=row.description,
        status=cast("AgentStatus", row.status),
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )


def _draft(row: Row[Any]) -> AgentDraft:
    return AgentDraft(
        draft_id=row.draft_id,
        agent_id=row.agent_id,
        workspace_id=row.workspace_id,
        revision=row.revision,
        status=cast("AgentDraftStatus", row.status),
        configuration=cast("dict[str, object]", row.configuration),
        config_hash=row.config_hash,
        updated_by_account_id=row.updated_by_account_id,
        updated_at=row.updated_at,
    )


def _revision(row: Row[Any]) -> AgentDraftRevision:
    return AgentDraftRevision(
        draft_id=row.draft_id,
        agent_id=row.agent_id,
        workspace_id=row.workspace_id,
        revision=row.revision,
        status=cast("AgentDraftStatus", row.status),
        configuration=cast("dict[str, object]", row.configuration),
        config_hash=row.config_hash,
        updated_by_account_id=row.updated_by_account_id,
        updated_at=row.updated_at,
    )


def _candidate(row: Row[Any]) -> AgentReleaseCandidate:
    return AgentReleaseCandidate(
        candidate_id=row.candidate_id,
        agent_id=row.agent_id,
        draft_id=row.draft_id,
        workspace_id=row.workspace_id,
        draft_revision=row.draft_revision,
        candidate_hash=row.candidate_hash,
        config_hash=row.config_hash,
        status=cast("AgentReleaseCandidateStatus", row.status),
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )


def _release(row: Row[Any]) -> AgentRelease:
    return AgentRelease(
        release_id=row.release_id,
        agent_id=row.agent_id,
        workspace_id=row.workspace_id,
        release_kind=cast("AgentReleaseKind", row.release_kind),
        version=row.version,
        runtime_config_version_id=row.runtime_config_version_id,
        config_hash=row.config_hash,
        candidate_id=row.candidate_id,
        candidate_hash=row.candidate_hash,
        snapshot=cast("dict[str, object] | None", row.snapshot),
        snapshot_hash=row.snapshot_hash,
        released_by_account_id=row.released_by_account_id,
        released_at=row.released_at,
    )


def _request(row: Row[Any]) -> AgentControlRequest:
    return AgentControlRequest(
        request_id=row.request_id,
        workspace_id=row.workspace_id,
        actor_id=row.actor_id,
        operation=row.operation,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        result_type=cast("AgentControlResultType", row.result_type),
        result_id=row.result_id,
        result_revision=row.result_revision,
        created_at=row.created_at,
    )


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None
