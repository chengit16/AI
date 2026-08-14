from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中读取服务配置，避免业务模块直接依赖进程环境。"""

    model_config = SettingsConfigDict(
        env_prefix="AI_PLATFORM_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "ai-platform-api"
    environment: str = "local"
    version: str = "0.0.0"
    dependency_checks_enabled: bool = False
    error_catalog_path: str = "contracts/errors/catalog.v1.json"
    release_manifest_path: str = "contracts/fixtures/release-manifest.v1.valid.json"
    compatibility_matrix_path: str = "contracts/release/compatibility-matrix.v1.json"
    resource_registry_path: str = "contracts/authorization/resource-registry.v1.json"
    field_policy_registry_path: str = "contracts/authorization/field-policy-registry.v1.json"
    master_key_path: str = ".ai-platform/secrets/master.key"
    database_url: str = "postgresql+psycopg://ai_platform@127.0.0.1:5432/ai_platform"
    valkey_url: str = "redis://127.0.0.1:6379/0"
    minio_endpoint: str = "http://127.0.0.1:9000"
    minio_access_key: str = "ai-platform-local"
    minio_secret_key: SecretStr = SecretStr("local-development-only")
    minio_bucket: str = "ai-platform-documents"
    upload_max_file_size_bytes: int = 20 * 1024 * 1024
    tika_url: str = "http://127.0.0.1:9998"
    session_ttl_seconds: int = 43_200
    session_cookie_secure: bool = False

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if not 300 <= self.session_ttl_seconds <= 86_400:
            raise ValueError("Session 有效期必须位于 5 分钟到 24 小时之间")
        if self.environment not in {"local", "test"} and not self.session_cookie_secure:
            raise ValueError("非本地环境必须启用 Secure Session Cookie")
        if not 1024 * 1024 <= self.upload_max_file_size_bytes <= 100 * 1024 * 1024:
            raise ValueError("上传大小上限必须位于 1 MiB 到 100 MiB 之间")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
