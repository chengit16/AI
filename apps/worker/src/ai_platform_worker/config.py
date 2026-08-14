"""加载 Worker、Celery、Outbox、解析和对象存储配置并校验安全边界。"""

from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """集中声明 Worker、Broker、对象存储、解析和租约相关运行配置。"""

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
    minio_endpoint: str = "http://127.0.0.1:9000"
    minio_access_key: str = "ai-platform-local"
    minio_secret_key: SecretStr = SecretStr("local-development-only")
    minio_bucket: str = "ai-platform-documents"
    tika_url: str = "http://127.0.0.1:9998"
    ingestion_batch_size: int = 4
    ingestion_lease_seconds: int = 120
    ingestion_retry_base_seconds: int = 5
    ingestion_dispatch_interval_seconds: float = 2.0
    ingestion_max_file_size_bytes: int = 20 * 1024 * 1024
    ingestion_max_page_count: int = 500
    indexing_batch_size: int = 4
    indexing_lease_seconds: int = 120
    indexing_max_attempts: int = 3
    indexing_retry_base_seconds: int = 5
    indexing_dispatch_interval_seconds: float = 2.0
    indexing_max_chunk_chars: int = 1_500
    indexing_chunk_overlap_chars: int = 150
    indexing_chunker_version: str = "structural-char-v1"

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
        ingestion_values = (
            self.ingestion_batch_size,
            self.ingestion_lease_seconds,
            self.ingestion_retry_base_seconds,
            self.ingestion_max_file_size_bytes,
            self.ingestion_max_page_count,
        )
        if any(value < 1 for value in ingestion_values):
            raise ValueError("入库任务整数参数必须为正数")
        if not 0.5 <= self.ingestion_dispatch_interval_seconds <= 60:
            raise ValueError("入库任务调度间隔必须位于 0.5 到 60 秒之间")
        indexing_values = (
            self.indexing_batch_size,
            self.indexing_lease_seconds,
            self.indexing_max_attempts,
            self.indexing_retry_base_seconds,
            self.indexing_max_chunk_chars,
        )
        if any(value < 1 for value in indexing_values):
            raise ValueError("索引任务整数参数必须为正数")
        if not 0.5 <= self.indexing_dispatch_interval_seconds <= 60:
            raise ValueError("索引任务调度间隔必须位于 0.5 到 60 秒之间")
        if not 0 <= self.indexing_chunk_overlap_chars < self.indexing_max_chunk_chars:
            raise ValueError("Chunk 重叠必须小于最大字符数")
        if not self.indexing_chunker_version.strip():
            raise ValueError("Chunker 版本不能为空")
        return self


@lru_cache
def get_worker_settings() -> WorkerSettings:
    """获取Worker设置，遵守任务幂等、有限重试和提交时机约束。"""

    return WorkerSettings()
