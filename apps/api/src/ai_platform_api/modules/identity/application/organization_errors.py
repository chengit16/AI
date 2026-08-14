"""定义组织管理用例的拒绝、不存在、冲突和校验错误。"""

from ai_platform_api.common.errors import PlatformError


class OrganizationGovernanceDeniedError(PlatformError):
    """个人空间、非所有者或非浏览器主体不能治理企业组织。"""

    error_code = "POLICY_DENIED"


class OrganizationConflictError(PlatformError):
    """部门环、同级重名、非法状态或成员归属冲突统一映射为稳定错误。"""

    error_code = "ORGANIZATION_CONFLICT"


class OrganizationNotFoundError(PlatformError):
    """表示组织未找到错误，由协议层映射为稳定错误码。"""

    error_code = "RESOURCE_NOT_FOUND"


class OrganizationValidationError(PlatformError):
    """表示组织校验错误，由协议层映射为稳定错误码。"""

    error_code = "VALIDATION_ERROR"
