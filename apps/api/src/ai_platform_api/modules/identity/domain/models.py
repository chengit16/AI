"""定义账号、工作空间、会话、密码和身份读取基础端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

AccountStatus = Literal["active", "disabled"]
WorkspaceStatus = Literal["active", "suspended", "archived"]
MembershipStatus = Literal["active", "disabled", "left"]
WorkspaceType = Literal["personal", "enterprise"]


@dataclass(frozen=True)
class AccountCredential:
    """提供认证所需的账号摘要，避免向认证层暴露完整账号记录。"""

    account_id: UUID
    login_name: str
    password_hash: str
    status: AccountStatus
    auth_version: int


@dataclass(frozen=True)
class WorkspaceAccess:
    """汇总账号进入某工作空间所需的空间与成员状态。"""

    workspace_id: UUID
    account_id: UUID
    workspace_status: WorkspaceStatus
    membership_status: MembershipStatus
    workspace_type: WorkspaceType = "enterprise"
    owner_account_id: UUID | None = None

    @property
    def active(self) -> bool:
        owner_allowed = (
            self.workspace_type != "personal" or self.owner_account_id == self.account_id
        )
        return (
            self.workspace_status == "active"
            and self.membership_status == "active"
            and owner_allowed
        )


@dataclass(frozen=True)
class BrowserSession:
    """保存服务端会话绑定的账号版本和 CSRF 摘要。"""

    account_id: UUID
    auth_version: int
    csrf_digest: str


@dataclass(frozen=True)
class LoginResult:
    """返回登录后仅一次交付给浏览器的会话与 CSRF 凭据。"""

    session_token: str
    csrf_token: str
    account_id: UUID
    personal_workspace_id: UUID


@dataclass(frozen=True)
class OpenApiKey:
    """表示已持久化的 OpenAPI Key 摘要、作用域与撤销状态。"""

    key_id: UUID
    actor_id: UUID
    workspace_id: UUID
    created_by_account_id: UUID
    secret_digest: str
    scopes: tuple[str, ...]
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class IssuedApiKey:
    """承载新签发 Key 的一次性明文及可长期展示的元数据。"""

    key_id: UUID
    actor_id: UUID
    workspace_id: UUID
    plaintext: str
    last_four: str
    scopes: tuple[str, ...]


class PasswordVerifier(Protocol):
    """隔离密码散列算法，使领域服务不依赖具体安全库。"""

    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str | None, password: str) -> bool: ...


class SecretDigester(Protocol):
    """为 API Key 等高熵凭据提供不可逆摘要与恒定语义比对。"""

    def digest(self, secret: str) -> str: ...

    def matches(self, expected_digest: str, secret: str) -> bool: ...


class IdentityReader(Protocol):
    """以最小只读投影解析账号、空间访问和 API Key 身份。"""

    def get_account_by_login(self, login_name: str) -> AccountCredential | None: ...

    def get_account(self, account_id: UUID) -> AccountCredential | None: ...

    def get_personal_workspace_id(self, account_id: UUID) -> UUID | None: ...

    def get_workspace_access(
        self,
        account_id: UUID,
        workspace_id: UUID,
    ) -> WorkspaceAccess | None: ...

    def get_api_key(self, key_id: UUID) -> OpenApiKey | None: ...


class ApiKeyWriter(Protocol):
    """在身份事务中持久化或撤销工作空间 API Key。"""

    def add(self, api_key: OpenApiKey, *, name: str, last_four: str) -> None: ...

    def revoke(self, workspace_id: UUID, key_id: UUID, revoked_at: datetime) -> bool: ...


class IdentityUnitOfWork(Protocol):
    """约束 API Key 写入使用显式事务和统一提交责任。"""

    @property
    def api_keys(self) -> ApiKeyWriter: ...

    def __enter__(self) -> IdentityUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class SessionStore(Protocol):
    """管理有过期时间的浏览器会话，存储实现不得保存会话明文令牌。"""

    def create(self, session: BrowserSession, ttl_seconds: int) -> str: ...

    def resolve(self, token: str) -> BrowserSession | None: ...

    def revoke(self, token: str) -> None: ...

    def close(self) -> None: ...
