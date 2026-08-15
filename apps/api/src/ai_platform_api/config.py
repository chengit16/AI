"""加载 API 进程配置并在启动前关闭不安全的生产默认值。"""

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
    master_key_version: int = 1
    database_url: str = "postgresql+psycopg://ai_platform@127.0.0.1:5432/ai_platform"
    valkey_url: str = "redis://127.0.0.1:6379/0"
    minio_endpoint: str = "http://127.0.0.1:9000"
    minio_access_key: str = "ai-platform-local"
    minio_secret_key: SecretStr = SecretStr("local-development-only")
    minio_bucket: str = "ai-platform-documents"
    upload_max_file_size_bytes: int = 20 * 1024 * 1024
    ingestion_max_attempts: int = 3
    tika_url: str = "http://127.0.0.1:9998"
    model_provider_allowed_hosts: tuple[str, ...] = ()
    model_provider_probe_timeout_seconds: int = 10
    stream_retention_seconds: int = 24 * 60 * 60
    stream_replay_limit_events: int = 5_000
    stream_replay_limit_bytes: int = 10 * 1024 * 1024
    stream_heartbeat_seconds: int = 15
    stream_poll_interval_ms: int = 250
    stream_delta_batch_characters: int = 512
    session_ttl_seconds: int = 43_200
    session_cookie_secure: bool = False
    local_mock_bootstrap_enabled: bool = False

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        # 1. 先约束认证与本地便利能力，生产环境不能继承开发态 Cookie 或 Mock 配置。
        if not 300 <= self.session_ttl_seconds <= 86_400:
            raise ValueError("Session 有效期必须位于 5 分钟到 24 小时之间")
        if self.environment not in {"local", "test"} and not self.session_cookie_secure:
            raise ValueError("非本地环境必须启用 Secure Session Cookie")
        if self.environment not in {"local", "test"} and self.local_mock_bootstrap_enabled:
            raise ValueError("内置 Mock 运行配置只能在 local 或 test 环境启用")
        if not 1 <= self.master_key_version <= 2_147_483_647:
            raise ValueError("平台主密钥版本必须是正的 32 位整数")
        # 2. 资源和流式预算必须处于已验证区间，避免配置错误绕过应用层有界处理。
        if not 1024 * 1024 <= self.upload_max_file_size_bytes <= 100 * 1024 * 1024:
            raise ValueError("上传大小上限必须位于 1 MiB 到 100 MiB 之间")
        if not 1 <= self.ingestion_max_attempts <= 10:
            raise ValueError("入库任务最大尝试次数必须位于 1 到 10 之间")
        if not 1 <= self.model_provider_probe_timeout_seconds <= 30:
            raise ValueError("模型供应商探测超时必须位于 1 秒到 30 秒之间")
        if not 60 <= self.stream_retention_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("SSE 事件保留期必须位于 1 分钟到 7 天之间")
        if not 1 <= self.stream_replay_limit_events <= 20_000:
            raise ValueError("SSE 单次回放事件数必须位于 1 到 20000 之间")
        if not 1_024 <= self.stream_replay_limit_bytes <= 50 * 1_024 * 1_024:
            raise ValueError("SSE 单次回放字节数必须位于 1 KiB 到 50 MiB 之间")
        if not 1 <= self.stream_heartbeat_seconds <= 60:
            raise ValueError("SSE 心跳间隔必须位于 1 到 60 秒之间")
        if not 50 <= self.stream_poll_interval_ms <= 5_000:
            raise ValueError("SSE 数据库轮询间隔必须位于 50 到 5000 毫秒之间")
        if not 64 <= self.stream_delta_batch_characters <= 4_096:
            raise ValueError("SSE 增量批次字符数必须位于 64 到 4096 之间")
        # 3. 供应商白名单只保存规范域名，URL、端口与路径统一由地址策略单独校验。
        normalized_hosts = tuple(
            host.strip().casefold().rstrip(".") for host in self.model_provider_allowed_hosts
        )
        if any(not host or "/" in host or ":" in host for host in normalized_hosts):
            raise ValueError("模型供应商允许列表只能包含不带端口和路径的域名")
        self.model_provider_allowed_hosts = tuple(sorted(set(normalized_hosts)))
        return self


@lru_cache
def get_settings() -> Settings:
    """读取并缓存进程配置；测试通过缓存清理显式切换环境。"""

    return Settings()
