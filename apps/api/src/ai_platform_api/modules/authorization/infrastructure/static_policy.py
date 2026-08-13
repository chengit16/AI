from dataclasses import dataclass, field
from uuid import UUID, uuid4

from ai_platform_api.modules.authorization.domain.policy import (
    Decision,
    PolicyDecision,
    PolicyRequest,
    ResourceScope,
)


@dataclass(frozen=True)
class PolicyGrant:
    permissions: frozenset[str]
    field_masks: dict[str, frozenset[str]] = field(default_factory=dict)
    resource_ids: dict[str, frozenset[UUID]] = field(default_factory=dict)


class StaticPolicyDecisionPoint:
    """阶段 0 的确定性策略 Adapter；未显式配置的主体和权限一律拒绝。"""

    def __init__(
        self,
        grants: dict[tuple[UUID, UUID], PolicyGrant],
        *,
        available: bool = True,
        policy_version: int = 1,
    ) -> None:
        self._grants = grants
        self._available = available
        self._policy_version = policy_version

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        decision: Decision = "deny"
        reason = "policy_unavailable" if not self._available else "permission_not_granted"
        grant = self._grants.get((request.context.actor_id, request.context.workspace_id))
        allowed_resources: frozenset[UUID] | None = None
        field_mask: frozenset[str] = frozenset()

        if request.resource.workspace_id != request.context.workspace_id:
            reason = "workspace_mismatch"
        elif self._available and grant and request.permission_code in grant.permissions:
            allowed_resources = grant.resource_ids.get(request.permission_code)
            if allowed_resources is None or request.resource.resource_id in allowed_resources:
                decision = "allow"
                reason = "explicit_grant"
                field_mask = grant.field_masks.get(request.permission_code, frozenset())
            else:
                reason = "resource_out_of_scope"

        return PolicyDecision(
            decision_id=uuid4(),
            decision=decision,
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(
                workspace=allowed_resources is None and decision == "allow",
                resource_ids=allowed_resources or frozenset(),
            ),
            field_mask=field_mask,
            policy_version=self._policy_version,
            cache_ttl_seconds=30 if decision == "allow" else 0,
            reason=reason,
        )
