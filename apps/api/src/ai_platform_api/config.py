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


@lru_cache
def get_settings() -> Settings:
    return Settings()
