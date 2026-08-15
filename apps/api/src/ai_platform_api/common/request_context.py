"""承载认证层建立的可信主体、工作空间、策略范围和 Trace 上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditAuthorization

from ai_platform_api.common.trace import TraceContext

if TYPE_CHECKING:
    from ai_platform_api.modules.authorization.domain.fields import SecurityLevel


@dataclass(frozen=True)
class RequestContext:
    """服务端认证链生成的可信请求上下文。"""

    request_id: UUID
    trace: TraceContext
    actor_id: UUID
    workspace_id: UUID
    user_id: UUID | None
    authentication_method: str
    credential_scopes: frozenset[str] | None
    authorized_permission_code: str | None = None
    authorized_policy_decision_id: UUID | None = None
    authorized_policy_version: int | None = None
    authorized_workspace: bool = False
    authorized_department_ids: frozenset[UUID] = frozenset()
    authorized_account_ids: frozenset[UUID] = frozenset()
    authorized_resource_ids: frozenset[UUID] = frozenset()
    authorized_field_mask: frozenset[str] = frozenset()
    authorized_maximum_security_level: SecurityLevel = "PUBLIC"

    @property
    def audit_authorization(self) -> AuditAuthorization | None:
        """仅在权限码、决策标识和策略版本完整时生成可追溯授权事实。"""

        if (
            self.authorized_permission_code is None
            or self.authorized_policy_decision_id is None
            or self.authorized_policy_version is None
        ):
            return None
        return AuditAuthorization(
            permission_code=self.authorized_permission_code,
            policy_decision_id=self.authorized_policy_decision_id,
            policy_version=self.authorized_policy_version,
        )

    @classmethod
    def trusted(
        cls,
        *,
        actor_id: UUID,
        workspace_id: UUID,
        trace: TraceContext,
        user_id: UUID | None = None,
        authentication_method: str = "test",
        credential_scopes: frozenset[str] | None = None,
        request_id: UUID | None = None,
    ) -> RequestContext:
        return cls(
            request_id=request_id or uuid4(),
            trace=trace,
            actor_id=actor_id,
            workspace_id=workspace_id,
            user_id=user_id,
            authentication_method=authentication_method,
            credential_scopes=credential_scopes,
        )


@dataclass(frozen=True)
class PlatformRequestContext:
    """平台治理请求不伪造工作空间，且只接受已登录的浏览器账号。"""

    request_id: UUID
    trace: TraceContext
    actor_id: UUID
    account_id: UUID
    authentication_method: str = "browser_session"
