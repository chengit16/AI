from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext

Decision = Literal["allow", "deny", "approval_required"]


@dataclass(frozen=True)
class ResourceReference:
    resource_type: str
    resource_id: UUID
    workspace_id: UUID
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyRequest:
    context: RequestContext
    permission_code: str
    resource: ResourceReference


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: UUID
    decision: Decision
    permission_code: str
    workspace_id: UUID
    resource_ids: frozenset[UUID] | None
    field_mask: frozenset[str]
    policy_version: int
    cache_ttl_seconds: int
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class PolicyDecisionPoint(Protocol):
    def decide(self, request: PolicyRequest) -> PolicyDecision: ...
