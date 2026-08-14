"""定义认证与注册用例可稳定映射的错误语义。"""

from ai_platform_api.common.errors import PlatformError


class AuthenticationRequiredError(PlatformError):
    """请求没有可验证凭证或凭证已经失效。"""

    error_code = "AUTH_REQUIRED"


class InvalidCredentialsError(PlatformError):
    """登录标识或密码无效时使用统一结果，避免枚举账号。"""

    error_code = "AUTH_INVALID_CREDENTIALS"


class RegistrationConflictError(PlatformError):
    """规范化登录名已被占用时返回稳定冲突，不暴露账号的其他状态。"""

    error_code = "REGISTRATION_CONFLICT"


class RegistrationValidationError(PlatformError):
    """注册输入没有满足应用层的规范化与密码安全约束。"""

    error_code = "VALIDATION_ERROR"


class CsrfValidationError(PlatformError):
    """浏览器写请求缺少与 Session 绑定的 CSRF 证明。"""

    error_code = "CSRF_INVALID"


class WorkspaceContextDeniedError(PlatformError):
    """工作空间不存在、停用或主体不是有效成员时统一拒绝。"""

    error_code = "POLICY_DENIED"


class WorkspaceRequiredError(PlatformError):
    """受保护请求没有可解析的工作空间标识。"""

    error_code = "WORKSPACE_REQUIRED"


class ApiKeyInvalidError(PlatformError):
    """Open API Key 缺失、过期、撤销或摘要不匹配。"""

    error_code = "AUTH_REQUIRED"


class ApiKeyConfigurationError(PlatformError):
    """Open API Key 名称、Scope 或有效期不符合安全约束。"""

    error_code = "VALIDATION_ERROR"


class ApiKeyNotFoundError(PlatformError):
    """Key 不存在或不属于当前空间时使用相同结果。"""

    error_code = "RESOURCE_NOT_FOUND"
