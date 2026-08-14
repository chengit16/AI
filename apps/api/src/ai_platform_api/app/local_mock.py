"""装配本地零配置问答所需的内置 Mock Provider 与运行配置。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.common.runtime import RuntimeConfigSnapshot
from ai_platform_api.common.security import EncryptedSecret, EnvelopeSecretCipher
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.model_gateway.application.runtime_configurations import (
    AiRuntimeConfigurationService,
)
from ai_platform_api.modules.model_gateway.domain.configuration import (
    ModelProviderConfiguration,
    ModelProviderCredential,
)
from ai_platform_api.modules.model_gateway.domain.models import GatewayPolicy, ModelCapability
from ai_platform_api.modules.model_gateway.domain.runtime import (
    AiRuntimeConfigVersion,
    RuntimeComponentVersions,
    RuntimeRouteDraft,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigInvalidError,
    AiRuntimeConfigNotActiveError,
)
from ai_platform_api.modules.model_gateway.infrastructure.configuration_sqlalchemy import (
    SessionFactory,
    SqlAlchemyModelProviderUnitOfWork,
)
from ai_platform_api.persistence.tables import accounts, platform_administrators

LOCAL_MOCK_PROVIDER_KEY = "local_mock_builtin"
LOCAL_MOCK_PROVIDER_DISPLAY_NAME = "本地 Mock Provider (内置)"
LOCAL_SYSTEM_ACCOUNT_ID = UUID("ffffffff-ffff-4fff-8fff-fffffffffff1")
LOCAL_MOCK_PROVIDER_ID = UUID("ffffffff-ffff-4fff-8fff-fffffffffff2")
LOCAL_MOCK_RUNTIME_NAME = "本地 Mock 运行配置 (内置)"
LOCAL_MOCK_API_KEY = "local-mock-key"
LOCAL_MOCK_BASE_URL = "https://local-mock.invalid/v1"
LOCAL_MOCK_TRACE = TraceContext("f" * 32, "e" * 16)
# 必须与运行配置版本分配锁 71610406 不同；自举内部会进入该版本分配事务。
LOCAL_MOCK_BOOTSTRAP_LOCK = 71_610_601
LOCAL_MOCK_CAPABILITIES: frozenset[ModelCapability] = frozenset(
    {"generation", "streaming", "tools", "structured_output"}
)


class LocalMockRuntimeBootstrap:
    """在未发布任何配置时，幂等建立仅供本地运行的模型基线。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        cipher: EnvelopeSecretCipher,
        runtime_configurations: AiRuntimeConfigurationService,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher
        self._runtime_configurations = runtime_configurations

    def ensure(self, account_id: UUID) -> RuntimeConfigSnapshot:
        """返回当前运行快照；已有真实配置时绝不创建或发布 Mock 配置。"""

        # 内置账号只承担本地审计主体，不能继承触发自举用户的身份或工作空间权限。
        del account_id
        self._ensure_system_principal()
        with self._session_factory() as lock_session, lock_session.begin():
            # PostgreSQL 事务锁串行化首次请求，避免并发创建重复 Provider 或运行版本。
            lock_session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": LOCAL_MOCK_BOOTSTRAP_LOCK},
            )
            return self._ensure_locked()

    def _ensure_locked(self) -> RuntimeConfigSnapshot:
        context = PlatformRequestContext(
            request_id=uuid4(),
            trace=LOCAL_MOCK_TRACE,
            actor_id=LOCAL_SYSTEM_ACCOUNT_ID,
            account_id=LOCAL_SYSTEM_ACCOUNT_ID,
        )
        current = self._runtime_configurations.current_configuration(context)
        if current is not None:
            if any(route.provider_id == LOCAL_MOCK_PROVIDER_ID for route in current.routes):
                # 当前快照引用内置路由时恢复被误停用的 Provider/凭据；真实路由不触发该逻辑。
                self._ensure_provider()
            return _runtime_snapshot(current.runtime_config_version_id, current.content_hash)

        configurations = self._runtime_configurations.list_configurations(context)
        local_configuration = next(
            (item for item in configurations if item.display_name == LOCAL_MOCK_RUNTIME_NAME),
            None,
        )
        if local_configuration is None and configurations:
            # 真实草稿存在但没有发布指针通常代表治理流程尚未完成，不能被本地便利逻辑覆盖。
            raise AiRuntimeConfigNotActiveError

        provider = self._ensure_provider()
        if local_configuration is not None:
            if not any(
                route.provider_id == provider.provider_id for route in local_configuration.routes
            ):
                raise AiRuntimeConfigInvalidError
            configuration = local_configuration
        else:
            configuration = self._create_runtime_configuration(context, provider.provider_id)
        self._runtime_configurations.activate(context, configuration.runtime_config_version_id)
        return _runtime_snapshot(
            configuration.runtime_config_version_id,
            configuration.content_hash,
        )

    def _ensure_system_principal(self) -> None:
        """先提交禁用账号和平台管理主体，使后续独立事务能够执行治理服务。"""

        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            session.execute(
                insert(accounts)
                .values(
                    account_id=LOCAL_SYSTEM_ACCOUNT_ID,
                    login_name="system.local.mock",
                    display_name="本地系统 Mock",
                    password_hash="disabled-local-system-account",
                    status="disabled",
                    auth_version=1,
                    created_at=now,
                    created_by_actor_id=LOCAL_SYSTEM_ACCOUNT_ID,
                    updated_at=now,
                    updated_by_actor_id=LOCAL_SYSTEM_ACCOUNT_ID,
                    version=1,
                )
                .on_conflict_do_nothing(index_elements=[accounts.c.account_id])
            )
            session.execute(
                insert(platform_administrators)
                .values(
                    account_id=LOCAL_SYSTEM_ACCOUNT_ID,
                    status="active",
                    granted_by_account_id=LOCAL_SYSTEM_ACCOUNT_ID,
                    granted_at=now,
                    revoked_at=None,
                )
                .on_conflict_do_update(
                    index_elements=[platform_administrators.c.account_id],
                    set_={"status": "active", "revoked_at": None},
                )
            )

    def _ensure_provider(self) -> ModelProviderConfiguration:
        now = datetime.now(UTC)
        with SqlAlchemyModelProviderUnitOfWork(self._session_factory) as unit_of_work:
            # 1. 固定身份只允许创建、恢复或读取内置 Provider，UUID/Key 冲突必须失败关闭。
            provider = unit_of_work.providers.get_configuration(
                LOCAL_MOCK_PROVIDER_ID,
                for_update=True,
            )
            if provider is None:
                provider = _new_local_provider(now)
                unit_of_work.providers.add_configuration(provider)
            elif provider.provider_key != LOCAL_MOCK_PROVIDER_KEY:
                # 固定 UUID 被其他配置占用代表数据库事实损坏，不能按名称猜测并继续调用。
                raise AiRuntimeConfigInvalidError
            elif provider.status != "active":
                provider = replace(
                    provider,
                    status="active",
                    updated_by_account_id=LOCAL_SYSTEM_ACCOUNT_ID,
                    updated_at=now,
                    version=provider.version + 1,
                )
                unit_of_work.providers.save_configuration(provider)

            # 2. 被撤销的占位凭据按递增版本恢复，旧密文保持不可变且不会重新激活。
            credential = unit_of_work.providers.get_active_credential(
                provider.provider_id,
                for_update=True,
            )
            if credential is None:
                credential_version = unit_of_work.providers.next_credential_version(
                    provider.provider_id
                )
                credential_id = uuid4()
                unit_of_work.providers.replace_active_credential(
                    ModelProviderCredential(
                        credential_id=credential_id,
                        provider_id=provider.provider_id,
                        credential_version=credential_version,
                        envelope=self._encrypt_credential(
                            provider.provider_id,
                            credential_id,
                            credential_version,
                        ),
                        status="active",
                        created_by_account_id=LOCAL_SYSTEM_ACCOUNT_ID,
                        created_at=now,
                        revoked_at=None,
                    )
                )
            # 3. Provider、凭据和平台审计同事务提交，不留下不可追溯的本地治理事实。
            unit_of_work.audit.add(
                account_id=LOCAL_SYSTEM_ACCOUNT_ID,
                provider_id=provider.provider_id,
                action="model_provider.local_mock_ensured",
                request_id=uuid4(),
                trace_id=LOCAL_MOCK_TRACE.trace_id,
                occurred_at=now,
                details={"provider_key": LOCAL_MOCK_PROVIDER_KEY},
            )
            unit_of_work.commit()
            return provider

    def _create_runtime_configuration(
        self,
        context: PlatformRequestContext,
        provider_id: UUID,
    ) -> AiRuntimeConfigVersion:
        return self._runtime_configurations.create(
            context,
            display_name=LOCAL_MOCK_RUNTIME_NAME,
            system_prompt_template="你是本地合成知识助手, 只能依据已提供的知识片段回答。",
            components=RuntimeComponentVersions(
                chunking="recursive-cjk-v1",
                embedding="deterministic-hash-1024-v1",
                index_schema="index-v1",
                reranker="deterministic-lexical-reranker-v1",
                retrieval="hybrid-rrf-v1",
                source_ranking="source-priority-v1",
                safety="rag-safety-v2",
                data_source_interface="data-source-v1",
                relevance_grader_interface="relevance-grader-v1",
                multimodal_router_interface="multimodal-router-v1",
            ),
            policy=GatewayPolicy(
                attempt_timeout_ms=500,
                total_timeout_ms=2_000,
                max_attempts_per_route=1,
                max_prompt_characters=32_000,
                max_output_tokens=2_048,
                max_response_characters=64_000,
                circuit_failure_threshold=3,
                circuit_recovery_ms=30_000,
                rule_degradation_message="本地 Mock 模型暂不可用, 请稍后重试",
                max_estimated_cost_microunits=5_000_000,
            ),
            routes=(
                RuntimeRouteDraft(
                    provider_id=provider_id,
                    priority=1,
                    model_id="local-mock-v1",
                    capabilities=frozenset({"generation", "streaming"}),
                    input_price_microunits_per_million_tokens=0,
                    output_price_microunits_per_million_tokens=0,
                ),
            ),
        )

    def _encrypt_credential(
        self,
        provider_id: UUID,
        credential_id: UUID,
        credential_version: int,
    ) -> EncryptedSecret:
        associated_data = (
            f"model-provider-credential:v1:{provider_id}:{credential_id}:{credential_version}"
        ).encode()
        return self._cipher.encrypt(LOCAL_MOCK_API_KEY, associated_data=associated_data)


def _new_local_provider(now: datetime) -> ModelProviderConfiguration:
    return ModelProviderConfiguration(
        provider_id=LOCAL_MOCK_PROVIDER_ID,
        provider_key=LOCAL_MOCK_PROVIDER_KEY,
        display_name=LOCAL_MOCK_PROVIDER_DISPLAY_NAME,
        adapter_kind="openai_compatible",
        base_url=LOCAL_MOCK_BASE_URL,
        probe_model_id="local-mock-v1",
        location="private",
        declared_capabilities=LOCAL_MOCK_CAPABILITIES,
        policy_review_status="approved",
        max_security_level="RESTRICTED",
        retention_days=0,
        training_usage_allowed=False,
        policy_url=None,
        policy_version="builtin-local-v1",
        policy_reviewed_by_account_id=LOCAL_SYSTEM_ACCOUNT_ID,
        policy_reviewed_at=now,
        probe_status="passed",
        probed_capabilities=LOCAL_MOCK_CAPABILITIES,
        last_probe_error_code=None,
        last_probed_at=now,
        status="active",
        created_by_account_id=LOCAL_SYSTEM_ACCOUNT_ID,
        created_at=now,
        updated_by_account_id=LOCAL_SYSTEM_ACCOUNT_ID,
        updated_at=now,
        version=1,
    )


def _runtime_snapshot(runtime_config_version_id: UUID, content_hash: str) -> RuntimeConfigSnapshot:
    return RuntimeConfigSnapshot(runtime_config_version_id, content_hash)
