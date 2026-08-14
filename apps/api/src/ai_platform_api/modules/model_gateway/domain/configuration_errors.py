from ai_platform_api.common.errors import PlatformError


class PlatformAdministratorRequiredError(PlatformError):
    error_code = "PLATFORM_ADMIN_REQUIRED"


class ModelProviderConfigurationInvalidError(PlatformError):
    error_code = "MODEL_PROVIDER_CONFIGURATION_INVALID"


class ModelProviderNotFoundError(PlatformError):
    error_code = "MODEL_PROVIDER_NOT_FOUND"


class ModelProviderConflictError(PlatformError):
    error_code = "MODEL_PROVIDER_CONFLICT"


class ModelProviderProbeFailedError(PlatformError):
    error_code = "MODEL_PROVIDER_PROBE_FAILED"


class ModelProviderDataPolicyDeniedError(PlatformError):
    error_code = "MODEL_PROVIDER_DATA_POLICY_DENIED"


class ModelProviderCredentialUnavailableError(PlatformError):
    error_code = "MODEL_PROVIDER_CREDENTIAL_UNAVAILABLE"
