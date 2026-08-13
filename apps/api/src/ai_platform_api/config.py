from functools import lru_cache

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
    database_url: str = "postgresql+psycopg://ai_platform@127.0.0.1:5432/ai_platform"
    redis_url: str = "redis://127.0.0.1:6379/0"
    minio_endpoint: str = "http://127.0.0.1:9000"
    tika_url: str = "http://127.0.0.1:9998"


@lru_cache
def get_settings() -> Settings:
    return Settings()
