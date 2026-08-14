"""定义套餐、功能开关和用量用例的稳定错误语义。"""

from ai_platform_api.common.errors import PlatformError


class EntitlementGovernanceDeniedError(PlatformError):
    """主体不是当前空间有效成员或没有套餐治理权限。"""

    error_code = "POLICY_DENIED"


class EntitlementFeatureDeniedError(PlatformError):
    """当前套餐没有启用目标功能。"""

    error_code = "ENTITLEMENT_DENIED"


class QuotaExceededError(PlatformError):
    """用量达到套餐上限或调整后会超过上限。"""

    error_code = "QUOTA_EXCEEDED"


class EntitlementConflictError(PlatformError):
    """表示权益冲突错误，由协议层映射为稳定错误码。"""

    error_code = "ENTITLEMENT_CONFLICT"


class EntitlementNotFoundError(PlatformError):
    """表示权益未找到错误，由协议层映射为稳定错误码。"""

    error_code = "RESOURCE_NOT_FOUND"


class EntitlementValidationError(PlatformError):
    """表示权益校验错误，由协议层映射为稳定错误码。"""

    error_code = "VALIDATION_ERROR"
