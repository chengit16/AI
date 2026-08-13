from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import insert, select, update
from sqlalchemy.engine import CursorResult, RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.authorization.domain.grants import system_role_permission_seed
from ai_platform_api.modules.identity.domain.entitlements import default_entitlement
from ai_platform_api.modules.identity.domain.models import (
    AccountCredential,
    AccountStatus,
    IdentityReader,
    IdentityUnitOfWork,
    OpenApiKey,
    WorkspaceAccess,
    WorkspaceType,
)
from ai_platform_api.modules.identity.domain.registration import (
    AccountRegistration,
    DuplicateLoginNameError,
)
from ai_platform_api.modules.identity.domain.roles import system_role_seed
from ai_platform_api.persistence.tables import (
    accounts,
    open_api_keys,
    role_bindings,
    role_permission_grants,
    roles,
    workspace_entitlements,
    workspace_feature_settings,
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

    def get_personal_workspace_id(self, account_id: UUID) -> UUID | None:
        with self._session_factory() as session:
            return session.execute(
                select(workspaces.c.workspace_id).where(
                    workspaces.c.owner_account_id == account_id,
                    workspaces.c.workspace_type == "personal",
                    workspaces.c.status == "active",
                )
            ).scalar_one_or_none()

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
                    workspaces.c.workspace_type,
                    workspaces.c.owner_account_id,
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
            workspace_type=cast("WorkspaceType", row.workspace_type),
            owner_account_id=cast(UUID | None, row.owner_account_id),
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
        self._state: ContextVar[tuple[Session, SqlAlchemyApiKeyWriter] | None] = ContextVar(
            "identity_unit_of_work", default=None
        )

    def __enter__(self) -> SqlAlchemyIdentityUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Identity Unit of Work 不允许在同一上下文重复进入")
        session = self._session_factory()
        api_keys = SqlAlchemyApiKeyWriter(session)
        self._state.set((session, api_keys))
        return self

    @property
    def api_keys(self) -> SqlAlchemyApiKeyWriter:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Identity Unit of Work 尚未进入事务范围")
        return state[1]

    def _session(self) -> Session:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Identity Unit of Work 尚未进入事务范围")
        return state[0]

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            session = state[0]
            if exc_type is not None:
                session.rollback()
            session.close()
            self._state.set(None)

    def commit(self) -> None:
        self._session().commit()


class SqlAlchemyRegistrationWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, registration: AccountRegistration) -> None:
        audit_values = {
            "created_at": registration.occurred_at,
            "created_by_actor_id": registration.account_id,
            "updated_at": registration.occurred_at,
            "updated_by_actor_id": registration.account_id,
            "version": 1,
        }
        try:
            self._session.execute(
                insert(accounts).values(
                    account_id=registration.account_id,
                    login_name=registration.login_name,
                    display_name=registration.display_name,
                    password_hash=registration.password_hash,
                    status="active",
                    auth_version=1,
                    **audit_values,
                )
            )
        except IntegrityError as error:
            if _is_login_conflict(error):
                raise DuplicateLoginNameError from error
            raise
        self._session.execute(
            insert(workspaces).values(
                workspace_id=registration.personal_workspace_id,
                workspace_type="personal",
                name=registration.personal_workspace_name,
                owner_account_id=registration.account_id,
                entitlement_version=1,
                role_version=1,
                status="active",
                **audit_values,
            )
        )
        self._session.execute(
            insert(workspace_memberships).values(
                membership_id=registration.membership_id,
                workspace_id=registration.personal_workspace_id,
                account_id=registration.account_id,
                membership_type="owner",
                status="active",
                created_at=registration.occurred_at,
                updated_at=registration.occurred_at,
                version=1,
            )
        )
        entitlement, feature_settings = default_entitlement(
            workspace_id=registration.personal_workspace_id,
            workspace_type="personal",
            occurred_at=registration.occurred_at,
        )
        self._session.execute(
            insert(workspace_entitlements).values(
                workspace_id=entitlement.workspace_id,
                plan_code=entitlement.plan_code,
                max_storage_bytes=entitlement.max_storage_bytes,
                max_members=entitlement.max_members,
                max_knowledge_bases=entitlement.max_knowledge_bases,
                max_published_agents=entitlement.max_published_agents,
                max_monthly_questions=entitlement.max_monthly_questions,
                open_api_allowed=entitlement.open_api_allowed,
                public_publish_allowed=entitlement.public_publish_allowed,
                created_at=entitlement.created_at,
                updated_at=entitlement.updated_at,
                version=entitlement.version,
            )
        )
        self._session.execute(
            insert(workspace_feature_settings).values(
                workspace_id=feature_settings.workspace_id,
                open_api_enabled=feature_settings.open_api_enabled,
                updated_at=feature_settings.updated_at,
                version=feature_settings.version,
            )
        )
        system_roles, system_bindings = system_role_seed(
            workspace_id=registration.personal_workspace_id,
            owner_membership_id=registration.membership_id,
            occurred_at=registration.occurred_at,
        )
        self._session.execute(
            insert(roles),
            [
                {
                    "role_id": role.role_id,
                    "workspace_id": role.workspace_id,
                    "role_key": role.role_key,
                    "name": role.name,
                    "status": role.status,
                    "system_managed": role.system_managed,
                    "created_at": role.created_at,
                    "updated_at": role.updated_at,
                    "version": role.version,
                }
                for role in system_roles
            ],
        )
        self._session.execute(
            insert(role_bindings),
            [
                {
                    "binding_id": binding.binding_id,
                    "workspace_id": binding.workspace_id,
                    "role_id": binding.role_id,
                    "scope_type": binding.scope_type,
                    "department_id": binding.department_id,
                    "membership_id": binding.membership_id,
                    "status": binding.status,
                    "created_at": binding.created_at,
                    "revoked_at": binding.revoked_at,
                    "version": binding.version,
                }
                for binding in system_bindings
            ],
        )
        owner_role, member_role = system_roles
        self._session.execute(
            insert(role_permission_grants),
            [
                {
                    "workspace_id": grant.workspace_id,
                    "role_id": grant.role_id,
                    "permission_code": grant.permission_code,
                    "scope_type": grant.scope_type,
                    "department_ids": list(grant.department_ids),
                    "resource_ids": list(grant.resource_ids),
                    "maximum_security_level": grant.maximum_security_level,
                    "field_mask": sorted(grant.field_mask),
                }
                for grant in system_role_permission_seed(
                    workspace_id=registration.personal_workspace_id,
                    owner_role_id=owner_role.role_id,
                    member_role_id=member_role.role_id,
                )
            ],
        )


class SqlAlchemyRegistrationUnitOfWork:
    """注册写入、审计与 Outbox 共用一个 Session 和一次提交。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyRegistrationWriter,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("registration_unit_of_work", default=None)

    def __enter__(self) -> None:
        if self._state.get() is not None:
            raise RuntimeError("Registration Unit of Work 不允许在同一上下文重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyRegistrationWriter(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyRegistrationWriter,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Registration Unit of Work 尚未进入事务范围")
        return state

    @property
    def registrations(self) -> SqlAlchemyRegistrationWriter:
        return self._current()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[3]

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            session = state[0]
            if exc_type is not None:
                session.rollback()
            session.close()
            self._state.set(None)

    def commit(self) -> None:
        session = self._current()[0]
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            if _is_login_conflict(error):
                raise DuplicateLoginNameError from error
            raise


def _is_login_conflict(error: IntegrityError) -> bool:
    if getattr(error.orig, "sqlstate", None) != "23505":
        return False
    diagnostics = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostics, "constraint_name", None)
    return constraint_name in {"accounts_login_name_key", "uq_accounts_login_name_normalized"}
