"""定义工具凭证版本、管理存储和受控调用边缘端口。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, TypeVar
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.security import EncryptedSecret
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt

ToolCredentialStatus = Literal["active", "revoked"]
T = TypeVar("T")


@dataclass(frozen=True)
class ToolCredential:
    """保存与 Workspace 和不可变工具版本精确绑定的信封密文。"""

    credential_id: UUID
    credential_ref: str
    workspace_id: UUID
    tool_id: UUID
    tool_version: int
    credential_version: int
    envelope: EncryptedSecret
    status: ToolCredentialStatus
    created_by_account_id: UUID
    created_at: datetime
    revoked_by_account_id: UUID | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class ToolCredentialBinding:
    """描述一次 ToolCall 已持久化的非敏感凭证引用。"""

    tool_call_id: UUID
    credential_ref: str
    credential_version: int


class ToolCredentialStore(Protocol):
    """独占工具凭证管理和 ToolCall 一次性引用绑定。"""

    def create(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        plaintext: str,
        created_at: datetime,
    ) -> ToolCredential: ...

    def rotate(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        plaintext: str,
        rotated_at: datetime,
    ) -> ToolCredential: ...

    def revoke(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        revoked_at: datetime,
    ) -> ToolCredential: ...

    def authorize_call(
        self,
        claim: ClaimedToolAttempt,
        *,
        authorized_at: datetime,
    ) -> ToolCredentialBinding: ...


class ToolCredentialCallEdge(Protocol):
    """只在持有当前活动凭证锁期间向受控 Adapter 回调注入明文。"""

    def invoke(
        self,
        claim: ClaimedToolAttempt,
        operation: Callable[[str], T],
        *,
        invoked_at: datetime,
    ) -> T: ...


def tool_credential_associated_data(
    *,
    workspace_id: UUID,
    tool_id: UUID,
    tool_version: int,
    credential_id: UUID,
    credential_ref: str,
    credential_version: int,
) -> bytes:
    """把密文认证绑定到空间、工具版本、凭证身份和轮换版本。"""

    return (
        "tool-credential:v1:"
        f"{workspace_id}:{tool_id}:{tool_version}:{credential_id}:"
        f"{credential_ref}:{credential_version}"
    ).encode()


__all__ = [
    "ToolCredential",
    "ToolCredentialBinding",
    "ToolCredentialCallEdge",
    "ToolCredentialStatus",
    "ToolCredentialStore",
    "tool_credential_associated_data",
]
