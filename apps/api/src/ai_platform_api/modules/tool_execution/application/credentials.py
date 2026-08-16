"""编排工具凭证的所有者管理、调用绑定与最窄边缘注入。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolCredentialUnavailableError,
    ToolExecutionDeniedError,
)
from ai_platform_api.modules.tool_execution.domain.credentials import (
    ToolCredential,
    ToolCredentialBinding,
    ToolCredentialCallEdge,
    ToolCredentialStore,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt

T = TypeVar("T")
_MIN_SECRET_LENGTH = 8
_MAX_SECRET_LENGTH = 4096


class ToolCredentialService:
    """保证凭证明文只进入写入加密边界或单次 Adapter 回调。"""

    def __init__(
        self,
        store: ToolCredentialStore,
        call_edge: ToolCredentialCallEdge,
    ) -> None:
        self._store = store
        self._call_edge = call_edge

    def create(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        plaintext: str,
        created_at: datetime | None = None,
    ) -> ToolCredential:
        """由当前 Workspace 所有者创建首个精确工具版本凭证。"""

        _require_browser_owner_candidate(context)
        _require_secret(plaintext, tool_version)
        return self._store.create(
            context,
            tool_id=tool_id,
            tool_version=tool_version,
            plaintext=plaintext,
            created_at=created_at or datetime.now(UTC),
        )

    def rotate(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        plaintext: str,
        rotated_at: datetime | None = None,
    ) -> ToolCredential:
        """撤销旧版本并创建新引用，已绑定旧引用的调用不能自动改绑。"""

        _require_browser_owner_candidate(context)
        _require_secret(plaintext, tool_version)
        return self._store.rotate(
            context,
            tool_id=tool_id,
            tool_version=tool_version,
            plaintext=plaintext,
            rotated_at=rotated_at or datetime.now(UTC),
        )

    def revoke(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        revoked_at: datetime | None = None,
    ) -> ToolCredential:
        """撤销当前活动凭证，后续绑定和调用边缘都必须立即失败关闭。"""

        _require_browser_owner_candidate(context)
        if tool_version < 1:
            raise ToolCredentialUnavailableError
        return self._store.revoke(
            context,
            tool_id=tool_id,
            tool_version=tool_version,
            revoked_at=revoked_at or datetime.now(UTC),
        )

    def authorize_call(
        self,
        claim: ClaimedToolAttempt,
        *,
        authorized_at: datetime | None = None,
    ) -> ToolCredentialBinding:
        """把当前活动引用和 `proposed → authorized` 作为一个数据库事实提交。"""

        return self._store.authorize_call(
            claim,
            authorized_at=authorized_at or datetime.now(UTC),
        )

    def invoke(
        self,
        claim: ClaimedToolAttempt,
        operation: Callable[[str], T],
        *,
        invoked_at: datetime | None = None,
    ) -> T:
        """仅在受控回调执行期间解密，普通调用方不能取得凭证返回值。"""

        return self._call_edge.invoke(
            claim,
            operation,
            invoked_at=invoked_at or datetime.now(UTC),
        )


def _require_browser_owner_candidate(context: RequestContext) -> None:
    """先拒绝非浏览器身份，最终所有者事实仍由数据库事务复核。"""

    if (
        context.authentication_method != "browser_session"
        or context.user_id is None
        or context.actor_id != context.user_id
    ):
        raise ToolExecutionDeniedError


def _require_secret(plaintext: str, tool_version: int) -> None:
    if (
        tool_version < 1
        or plaintext != plaintext.strip()
        or not _MIN_SECRET_LENGTH <= len(plaintext) <= _MAX_SECRET_LENGTH
        or "\x00" in plaintext
    ):
        raise ToolCredentialUnavailableError


__all__ = ["ToolCredentialService"]
