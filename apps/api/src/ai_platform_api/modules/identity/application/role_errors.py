"""定义角色配置、绑定和继承计算用例的稳定错误语义。"""

from ai_platform_api.common.errors import PlatformError


class RoleGovernanceDeniedError(PlatformError):
    """非企业所有者不能修改角色，成员只能读取自己的有效角色。"""

    error_code = "POLICY_DENIED"


class RoleConflictError(PlatformError):
    """角色状态、重名、重复绑定或绑定范围冲突。"""

    error_code = "ROLE_CONFLICT"


class RoleNotFoundError(PlatformError):
    """表示角色未找到错误，由协议层映射为稳定错误码。"""

    error_code = "RESOURCE_NOT_FOUND"


class RoleValidationError(PlatformError):
    """表示角色校验错误，由协议层映射为稳定错误码。"""

    error_code = "VALIDATION_ERROR"
