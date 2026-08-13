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
    error_code = "ENTITLEMENT_CONFLICT"


class EntitlementNotFoundError(PlatformError):
    error_code = "RESOURCE_NOT_FOUND"


class EntitlementValidationError(PlatformError):
    error_code = "VALIDATION_ERROR"
