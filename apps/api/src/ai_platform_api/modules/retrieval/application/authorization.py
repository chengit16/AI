"""集中把可信请求和 PDP 决策收敛为检索可执行授权。"""

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import FieldPolicyRegistry
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyRequest,
    ResourceReference,
)
from ai_platform_api.modules.retrieval.domain.errors import RetrievalScopeDeniedError
from ai_platform_api.modules.retrieval.domain.planning import (
    RetrievalAuthorization,
    RetrievalPlanSnapshot,
    RetrievalRunInput,
)


def retrieval_policy_request(context: RequestContext) -> PolicyRequest:
    """构造服务端固定的知识文档读取授权请求，拒绝调用方覆盖权限码。"""

    return PolicyRequest(
        context=context,
        permission_code="knowledge.document.read",
        resource=ResourceReference(
            resource_type="document",
            resource_id=context.workspace_id,
            workspace_id=context.workspace_id,
            attributes={"risk_level": "high"},
        ),
        surface="retrieval",
    )


def resolve_retrieval_authorization(
    decision: PolicyDecision,
    field_registry: FieldPolicyRegistry,
) -> RetrievalAuthorization:
    """合并 PDP 与字段注册表决策，正文受限时立即失败关闭。"""

    if not decision.allowed:
        raise RetrievalScopeDeniedError
    chunk_mask = field_registry.field_mask(
        "chunk",
        decision.maximum_security_level,
        {},
    ) | frozenset(
        field_name
        for field_name in decision.field_mask
        if field_name in field_registry.fields_for("chunk")
    )
    if "content" in chunk_mask:
        raise RetrievalScopeDeniedError
    return RetrievalAuthorization(
        decision_id=decision.decision_id,
        policy_version=decision.policy_version,
        workspace_wide=decision.resource_scope.workspace,
        department_ids=decision.resource_scope.department_ids,
        account_ids=decision.resource_scope.account_ids,
        resource_ids=decision.resource_scope.resource_ids,
        maximum_security_level=decision.maximum_security_level,
        field_mask=chunk_mask,
    )


def same_retrieval_requester(
    context: RequestContext,
    run: RetrievalRunInput,
    *,
    allowed_statuses: frozenset[str] = frozenset({"queued", "running"}),
) -> bool:
    """确认运行属于当前浏览器主体，并且处于调用方明确允许的状态。"""

    return (
        context.workspace_id == run.workspace_id
        and context.user_id == run.requested_by_account_id
        and context.actor_id == run.requested_by_account_id
        and context.authentication_method == "browser_session"
        and run.status in allowed_statuses
    )


def same_retrieval_authorization(
    plan: RetrievalPlanSnapshot,
    authorization: RetrievalAuthorization,
) -> bool:
    """拒绝在策略版本、密级或字段遮罩变化后沿用旧候选快照。"""

    return (
        plan.policy_version == authorization.policy_version
        and plan.maximum_security_level == authorization.maximum_security_level
        and plan.field_mask == authorization.field_mask
    )
