"""定义账号与默认个人空间注册聚合和同事务写入端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter


@dataclass(frozen=True)
class AccountRegistration:
    """注册事务的完整事实，确保账号、个人空间和所有者关系不会分开创建。"""

    account_id: UUID
    login_name: str
    display_name: str
    password_hash: str
    personal_workspace_id: UUID
    membership_id: UUID
    personal_workspace_name: str
    occurred_at: datetime


@dataclass(frozen=True)
class RegistrationResult:
    """返回新账号及其默认个人空间标识。"""

    account_id: UUID
    personal_workspace_id: UUID


class DuplicateLoginNameError(Exception):
    """数据库唯一约束拒绝规范化登录名时由 Adapter 转换为领域冲突。"""


class RegistrationWriter(Protocol):
    """一次写入账号、个人空间、所有者成员、系统角色和默认权益。"""

    def add(self, registration: AccountRegistration) -> None: ...


class RegistrationUnitOfWork(Protocol):
    """保证账号、个人空间、审计和 Outbox 在同一事务内提交。"""

    @property
    def registrations(self) -> RegistrationWriter: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> None: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
