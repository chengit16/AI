"""实现同 SQL 工作空间隔离资源查询与共享事务 UoW。"""

from collections.abc import Callable
from types import TracebackType
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ai_platform_api.modules.integration.domain.events import OutboxWriter
from ai_platform_api.modules.workspace.domain.resource import (
    WorkspaceResource,
    WorkspaceResourceRepository,
)
from ai_platform_api.persistence.tables import workspace_resources


class SqlAlchemyWorkspaceResourceRepository:
    """使用复合工作空间条件读写资源，跨空间标识不会返回记录。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, resource: WorkspaceResource) -> None:
        self._session.execute(
            insert(workspace_resources).values(
                resource_id=resource.resource_id,
                workspace_id=resource.workspace_id,
                title=resource.title,
                sensitive_value=resource.sensitive_value,
                version=resource.version,
            )
        )

    def get_scoped(self, workspace_id: UUID, resource_id: UUID) -> WorkspaceResource | None:
        # 空间条件与资源 ID 位于同一个 SQL，避免先查资源再判断空间造成存在性泄漏。
        row = self._session.execute(
            select(workspace_resources).where(
                workspace_resources.c.workspace_id == workspace_id,
                workspace_resources.c.resource_id == resource_id,
            )
        ).one_or_none()
        if row is None:
            return None
        return WorkspaceResource(
            resource_id=row.resource_id,
            workspace_id=row.workspace_id,
            title=row.title,
            sensitive_value=row.sensitive_value,
            version=row.version,
        )


class SqlAlchemyWorkspaceUnitOfWork:
    """保证资源、审计和 Outbox 使用同一 SQLAlchemy Session 提交。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        outbox_factory: Callable[[Session], OutboxWriter],
        audit_factory: Callable[[Session], AuditWriter],
    ) -> None:
        self._session_factory = session_factory
        self._outbox_factory = outbox_factory
        self._audit_factory = audit_factory
        self._session: Session | None = None
        self.resources: WorkspaceResourceRepository
        self.outbox: OutboxWriter
        self.audit: AuditWriter

    def __enter__(self) -> "SqlAlchemyWorkspaceUnitOfWork":
        self._session = self._session_factory()
        self.resources = SqlAlchemyWorkspaceResourceRepository(self._session)
        # 跨模块基础设施由装配入口注入，工作空间模块只依赖 Outbox 领域端口。
        self.outbox = self._outbox_factory(self._session)
        self.audit = self._audit_factory(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            if exc_type is not None:
                self._session.rollback()
            self._session.close()
            self._session = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Workspace Unit of Work 尚未进入事务范围")
        self._session.commit()
