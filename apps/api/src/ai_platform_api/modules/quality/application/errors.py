"""定义质量样本、评估与运营门禁的稳定应用错误。"""

from ai_platform_api.common.errors import PlatformError


class QualityDeniedError(PlatformError):
    """授权投影不完整、字段受限或资源范围不覆盖当前操作。"""

    error_code = "POLICY_DENIED"


class QualityValidationError(PlatformError):
    """质量样本来源、正文或安全级别不符合稳定契约。"""

    error_code = "VALIDATION_ERROR"


class QualityConflictError(PlatformError):
    """同一来源版本已指向不同内容或版本发生倒退。"""

    error_code = "IDEMPOTENCY_CONFLICT"


class QualityNotFoundError(PlatformError):
    """目标质量评估或运营窗口在当前工作空间不可见。"""

    error_code = "RESOURCE_NOT_FOUND"
