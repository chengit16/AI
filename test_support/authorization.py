from uuid import uuid4

from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyRequest,
    ResourceScope,
)


class AllowRegisteredPolicy:
    """API 映射测试只验证协议；真实拒绝路径由 P1C-02 专项测试覆盖。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=uuid4(),
            decision="allow",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(workspace=True),
            field_mask=frozenset(),
            policy_version=1,
            cache_ttl_seconds=0,
            reason="synthetic_api_contract_test",
        )
