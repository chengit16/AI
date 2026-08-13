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
    account_id: UUID
    login_name: str
    password_hash: str
    status: AccountStatus
    auth_version: int


@dataclass(frozen=True)
class WorkspaceAccess:
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
    account_id: UUID
    auth_version: int
    csrf_digest: str


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    csrf_token: str
    account_id: UUID
    personal_workspace_id: UUID


@dataclass(frozen=True)
class OpenApiKey:
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
    key_id: UUID
    actor_id: UUID
    workspace_id: UUID
    plaintext: str
    last_four: str
    scopes: tuple[str, ...]


class PasswordVerifier(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str | None, password: str) -> bool: ...


class SecretDigester(Protocol):
    def digest(self, secret: str) -> str: ...

    def matches(self, expected_digest: str, secret: str) -> bool: ...


class IdentityReader(Protocol):
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
    def add(self, api_key: OpenApiKey, *, name: str, last_four: str) -> None: ...

    def revoke(self, workspace_id: UUID, key_id: UUID, revoked_at: datetime) -> bool: ...


class IdentityUnitOfWork(Protocol):
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
    def create(self, session: BrowserSession, ttl_seconds: int) -> str: ...

    def resolve(self, token: str) -> BrowserSession | None: ...

    def revoke(self, token: str) -> None: ...

    def close(self) -> None: ...
