from ai_platform_api.common.errors import PlatformError


class RoleGovernanceDeniedError(PlatformError):
    """非企业所有者不能修改角色，成员只能读取自己的有效角色。"""

    error_code = "POLICY_DENIED"


class RoleConflictError(PlatformError):
    """角色状态、重名、重复绑定或绑定范围冲突。"""

    error_code = "ROLE_CONFLICT"


class RoleNotFoundError(PlatformError):
    error_code = "RESOURCE_NOT_FOUND"


class RoleValidationError(PlatformError):
    error_code = "VALIDATION_ERROR"
