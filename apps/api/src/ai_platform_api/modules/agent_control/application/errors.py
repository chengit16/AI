"""定义 Agent 控制面稳定应用错误，供协议层统一映射错误码。"""

from ai_platform_api.common.errors import PlatformError


class AgentDeniedError(PlatformError):
    """表示可信上下文没有目标 Agent 资源范围。"""

    error_code = "POLICY_DENIED"


class AgentNotFoundError(PlatformError):
    """表示 Agent 控制面事实在当前工作空间内不可见。"""

    error_code = "RESOURCE_NOT_FOUND"


class AgentValidationError(PlatformError):
    """表示名称、配置文档、revision 或幂等键不符合稳定约束。"""

    error_code = "VALIDATION_ERROR"


class AgentConfigurationInvalidError(PlatformError):
    """表示 Agent 配置结构、资源引用、安全边界或预算校验失败。"""

    error_code = "AGENT_CONFIGURATION_INVALID"


class AgentTestGateFailedError(PlatformError):
    """表示候选缺少完整通过且与当前摘要一致的确定性测试证据。"""

    error_code = "AGENT_TEST_GATE_FAILED"


class AgentReleaseApprovalRequiredError(PlatformError):
    """表示候选缺少与当前摘要和测试结果匹配的有效发布审批。"""

    error_code = "AGENT_RELEASE_APPROVAL_REQUIRED"


class AgentLifecycleConflictError(PlatformError):
    """表示 Agent 状态、草稿 revision 或候选来源已经发生竞争变化。"""

    error_code = "AGENT_LIFECYCLE_CONFLICT"


class AgentIdempotencyConflictError(PlatformError):
    """表示同一幂等键已经绑定不同的 Agent 控制面请求。"""

    error_code = "IDEMPOTENCY_CONFLICT"
