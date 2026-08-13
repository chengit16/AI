from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.engine import CursorResult, RowMapping
from sqlalchemy.orm import Session

from ai_platform_api.modules.identity.domain.models import (
    AccountCredential,
    AccountStatus,
    IdentityReader,
    IdentityUnitOfWork,
    OpenApiKey,
    WorkspaceAccess,
)
from ai_platform_api.persistence.tables import (
    accounts,
    open_api_keys,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyIdentityReader(IdentityReader):
    """每次读取使用独立短 Session，避免认证服务跨请求共享事务状态。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def get_account_by_login(self, login_name: str) -> AccountCredential | None:
        with self._session_factory() as session:
            row = session.execute(
                select(
                    accounts.c.account_id,
                    accounts.c.login_name,
                    accounts.c.password_hash,
                    accounts.c.status,
                    accounts.c.auth_version,
                ).where(accounts.c.login_name == login_name)
            ).one_or_none()
        return self._account(row._mapping if row is not None else None)

    def get_account(self, account_id: UUID) -> AccountCredential | None:
        with self._session_factory() as session:
            row = session.execute(
                select(
                    accounts.c.account_id,
                    accounts.c.login_name,
                    accounts.c.password_hash,
                    accounts.c.status,
                    accounts.c.auth_version,
                ).where(accounts.c.account_id == account_id)
            ).one_or_none()
        return self._account(row._mapping if row is not None else None)

    def get_workspace_access(
        self,
        account_id: UUID,
        workspace_id: UUID,
    ) -> WorkspaceAccess | None:
        # 账号与空间条件位于同一查询，客户端声明的 workspace_id 不能绕过成员关系。
        with self._session_factory() as session:
            row = session.execute(
                select(
                    workspace_memberships.c.workspace_id,
                    workspace_memberships.c.account_id,
                    workspaces.c.status.label("workspace_status"),
                    workspace_memberships.c.status.label("membership_status"),
                )
                .join(
                    workspaces,
                    workspaces.c.workspace_id == workspace_memberships.c.workspace_id,
                )
                .where(
                    workspace_memberships.c.account_id == account_id,
                    workspace_memberships.c.workspace_id == workspace_id,
                )
            ).one_or_none()
        if row is None:
            return None
        return WorkspaceAccess(
            workspace_id=row.workspace_id,
            account_id=row.account_id,
            workspace_status=row.workspace_status,
            membership_status=row.membership_status,
        )

    def get_api_key(self, key_id: UUID) -> OpenApiKey | None:
        with self._session_factory() as session:
            row = session.execute(
                select(open_api_keys).where(open_api_keys.c.key_id == key_id)
            ).one_or_none()
        if row is None:
            return None
        return OpenApiKey(
            key_id=row.key_id,
            actor_id=row.actor_id,
            workspace_id=row.workspace_id,
            created_by_account_id=row.created_by_account_id,
            secret_digest=row.secret_digest,
            scopes=tuple(row.scopes),
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
        )

    @staticmethod
    def _account(row: RowMapping | None) -> AccountCredential | None:
        if row is None:
            return None
        return AccountCredential(
            account_id=cast(UUID, row["account_id"]),
            login_name=cast(str, row["login_name"]),
            password_hash=cast(str, row["password_hash"]),
            status=cast("AccountStatus", row["status"]),
            auth_version=cast(int, row["auth_version"]),
        )


class SqlAlchemyApiKeyWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, api_key: OpenApiKey, *, name: str, last_four: str) -> None:
        self._session.execute(
            insert(open_api_keys).values(
                key_id=api_key.key_id,
                actor_id=api_key.actor_id,
                workspace_id=api_key.workspace_id,
                created_by_account_id=api_key.created_by_account_id,
                name=name,
                secret_digest=api_key.secret_digest,
                last_four=last_four,
                scopes=list(api_key.scopes),
                created_at=datetime.now(UTC),
                expires_at=api_key.expires_at,
                revoked_at=api_key.revoked_at,
            )
        )

    def revoke(self, workspace_id: UUID, key_id: UUID, revoked_at: datetime) -> bool:
        result = cast(
            CursorResult[object],
            self._session.execute(
                update(open_api_keys)
                .where(
                    open_api_keys.c.workspace_id == workspace_id,
                    open_api_keys.c.key_id == key_id,
                    open_api_keys.c.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            ),
        )
        return bool(result.rowcount == 1)


class SqlAlchemyIdentityUnitOfWork(IdentityUnitOfWork):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> SqlAlchemyIdentityUnitOfWork:
        self._session = self._session_factory()
        self.api_keys = SqlAlchemyApiKeyWriter(self._session)
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
            raise RuntimeError("Identity Unit of Work 尚未进入事务范围")
        self._session.commit()
