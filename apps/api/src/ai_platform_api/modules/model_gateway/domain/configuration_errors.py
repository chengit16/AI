"""定义模型供应商平台治理的稳定错误语义。"""

from ai_platform_api.common.errors import PlatformError


class PlatformAdministratorRequiredError(PlatformError):
    """表示平台管理员权限不足错误，由协议层映射为稳定错误码。"""

    error_code = "PLATFORM_ADMIN_REQUIRED"


class ModelProviderConfigurationInvalidError(PlatformError):
    """表示模型供应商配置无效错误，由协议层映射为稳定错误码。"""

    error_code = "MODEL_PROVIDER_CONFIGURATION_INVALID"


class ModelProviderNotFoundError(PlatformError):
    """表示模型供应商未找到错误，由协议层映射为稳定错误码。"""

    error_code = "MODEL_PROVIDER_NOT_FOUND"


class ModelProviderConflictError(PlatformError):
    """表示模型供应商冲突错误，由协议层映射为稳定错误码。"""

    error_code = "MODEL_PROVIDER_CONFLICT"


class ModelProviderProbeFailedError(PlatformError):
    """表示模型供应商探测失败错误，由协议层映射为稳定错误码。"""

    error_code = "MODEL_PROVIDER_PROBE_FAILED"


class ModelProviderDataPolicyDeniedError(PlatformError):
    """表示模型供应商数据策略拒绝错误，由协议层映射为稳定错误码。"""

    error_code = "MODEL_PROVIDER_DATA_POLICY_DENIED"


class ModelProviderCredentialUnavailableError(PlatformError):
    """表示模型供应商凭据不可用错误，由协议层映射为稳定错误码。"""

    error_code = "MODEL_PROVIDER_CREDENTIAL_UNAVAILABLE"
