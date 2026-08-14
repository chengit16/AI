"""定义统一策略请求、范围决策和默认拒绝决策端口。"""

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel

Decision = Literal["allow", "deny", "approval_required"]
DataScopeType = Literal["workspace", "department_tree", "self", "resource"]


@dataclass(frozen=True)
class ResourceReference:
    """标识授权请求中的资源类型、工作空间和可选具体资源。"""

    resource_type: str
    resource_id: UUID
    workspace_id: UUID
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyRequest:
    """定义策略操作的请求字段与协议校验边界。"""

    context: RequestContext
    permission_code: str
    resource: ResourceReference


@dataclass(frozen=True)
class ResourceScope:
    """策略只返回可执行范围事实，业务 Repository 负责把它转换为查询条件。"""

    workspace: bool = False
    department_ids: frozenset[UUID] = frozenset()
    account_ids: frozenset[UUID] = frozenset()
    resource_ids: frozenset[UUID] = frozenset()

    @property
    def unrestricted(self) -> bool:
        return self.workspace


@dataclass(frozen=True)
class PolicyDecision:
    """返回允许/拒绝结论、数据范围、字段掩码和可解释原因。"""

    decision_id: UUID
    decision: Decision
    permission_code: str
    workspace_id: UUID
    resource_scope: ResourceScope
    field_mask: frozenset[str]
    policy_version: int
    cache_ttl_seconds: int
    reason: str
    maximum_security_level: SecurityLevel = "PUBLIC"

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def resource_ids(self) -> frozenset[UUID] | None:
        """兼容阶段 0 消费者；工作空间范围继续使用 None 表示不限制具体资源。"""

        if self.resource_scope.workspace:
            return None
        return self.resource_scope.resource_ids


class PolicyDecisionPoint(Protocol):
    """对可信主体、动作和资源执行统一策略决策，未知情况默认拒绝。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision: ...
