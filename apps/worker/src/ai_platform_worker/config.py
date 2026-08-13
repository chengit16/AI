from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_PLATFORM_",
        env_file=".env",
        extra="ignore",
    )

    environment: str = "local"
    version: str = "0.0.0"
    database_url: str = "postgresql+psycopg://ai_platform@127.0.0.1:5432/ai_platform"
    valkey_url: str = "redis://127.0.0.1:6379/0"
    task_signing_key_path: str = ".ai-platform/secrets/task-signing.key"
    outbox_batch_size: int = 50
    outbox_lease_seconds: int = 60
    outbox_max_attempts: int = 5
    outbox_retry_base_seconds: int = 5
    outbox_dispatch_interval_seconds: float = 2.0

    @model_validator(mode="after")
    def validate_outbox_settings(self) -> "WorkerSettings":
        values = (
            self.outbox_batch_size,
            self.outbox_lease_seconds,
            self.outbox_max_attempts,
            self.outbox_retry_base_seconds,
        )
        if any(value < 1 for value in values):
            raise ValueError("Outbox 整数参数必须为正数")
        if not 0.5 <= self.outbox_dispatch_interval_seconds <= 60:
            raise ValueError("Outbox 调度间隔必须位于 0.5 到 60 秒之间")
        return self


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
