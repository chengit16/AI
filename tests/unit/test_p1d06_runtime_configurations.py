from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.model_gateway.application.runtime_configurations import (
    AiRuntimeConfigurationService,
)
from ai_platform_api.modules.model_gateway.domain.configuration import (
    ModelProviderConfiguration,
)
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    PlatformAdministratorRequiredError,
)
from ai_platform_api.modules.model_gateway.domain.models import GatewayPolicy
from ai_platform_api.modules.model_gateway.domain.runtime import (
    AiRuntimeConfigPublication,
    AiRuntimeConfigVersion,
    RuntimeComponentVersions,
    RuntimeRouteDraft,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigConflictError,
    AiRuntimeConfigInvalidError,
)

ADMIN_ID = UUID("10000000-0000-4000-8000-000000000606")
MEMBER_ID = UUID("10000000-0000-4000-8000-000000000607")
PRIMARY_ID = UUID("20000000-0000-4000-8000-000000000606")
BACKUP_ID = UUID("20000000-0000-4000-8000-000000000607")
NOW = datetime(2026, 8, 14, tzinfo=UTC)


class MemoryRuntimeRepository:
    def __init__(self) -> None:
        self.administrators = {ADMIN_ID}
        self.providers = {
            PRIMARY_ID: provider(PRIMARY_ID, "external"),
            BACKUP_ID: provider(BACKUP_ID, "private"),
        }
        self.configurations: list[AiRuntimeConfigVersion] = []
        self.publication: AiRuntimeConfigPublication | None = None

    def is_platform_administrator(self, account_id: UUID) -> bool:
        return account_id in self.administrators

    def list_configurations(self) -> tuple[AiRuntimeConfigVersion, ...]:
        return tuple(reversed(self.configurations))

    def get_configuration(
        self, runtime_config_version_id: UUID, *, for_update: bool = False
    ) -> AiRuntimeConfigVersion | None:
        del for_update
        return next(
            (
                item
                for item in self.configurations
                if item.runtime_config_version_id == runtime_config_version_id
            ),
            None,
        )

    def get_current_publication(
        self, *, for_update: bool = False
    ) -> AiRuntimeConfigPublication | None:
        del for_update
        return self.publication

    def get_provider_configuration(self, provider_id: UUID) -> ModelProviderConfiguration | None:
        return self.providers.get(provider_id)

    def next_version_number(self) -> int:
        return len(self.configurations) + 1

    def add_configuration(self, configuration: AiRuntimeConfigVersion) -> None:
        if any(item.content_hash == configuration.content_hash for item in self.configurations):
            raise AiRuntimeConfigConflictError
        self.configurations.append(configuration)

    def publish(self, publication: AiRuntimeConfigPublication) -> None:
        self.publication = publication


class MemoryAudit:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def add(self, **values: object) -> None:
        self.actions.append(str(values["action"]))


class MemoryUnitOfWork:
    def __init__(self) -> None:
        self.runtime_configs = MemoryRuntimeRepository()
        self.audit = MemoryAudit()

    def __enter__(self) -> MemoryUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        return None


def provider(provider_id: UUID, location: str) -> ModelProviderConfiguration:
    return ModelProviderConfiguration(
        provider_id=provider_id,
        provider_key=f"provider_{str(provider_id)[-3:]}",
        display_name="合成供应商",
        adapter_kind="openai_compatible",
        base_url="https://api.synthetic.example/v1",
        probe_model_id="synthetic-model",
        location=location,  # type: ignore[arg-type]
        declared_capabilities=frozenset({"generation", "streaming"}),
        policy_review_status="approved",
        max_security_level="CONFIDENTIAL",
        retention_days=0,
        training_usage_allowed=False,
        policy_url="https://policy.synthetic.example/privacy",
        policy_version="v1",
        policy_reviewed_by_account_id=ADMIN_ID,
        policy_reviewed_at=NOW,
        probe_status="passed",
        probed_capabilities=frozenset({"generation", "streaming"}),
        last_probe_error_code=None,
        last_probed_at=NOW,
        status="active",
        created_by_account_id=ADMIN_ID,
        created_at=NOW,
        updated_by_account_id=ADMIN_ID,
        updated_at=NOW,
        version=4,
    )


def context(account_id: UUID = ADMIN_ID) -> PlatformRequestContext:
    return PlatformRequestContext(
        request_id=UUID("90000000-0000-4000-8000-000000000606"),
        trace=TraceContext("6" * 32, "7" * 16),
        actor_id=account_id,
        account_id=account_id,
    )


def components() -> RuntimeComponentVersions:
    return RuntimeComponentVersions(
        chunking="recursive-cjk-v1",
        embedding="deterministic-hash-1024-v1",
        index_schema="index-v1",
        reranker="bge-reranker-v1",
        retrieval="hybrid-rrf-v1",
        source_ranking="source-priority-v1",
        safety="rag-safety-v1",
        data_source_interface="data-source-v1",
        relevance_grader_interface="relevance-grader-v1",
        multimodal_router_interface="multimodal-router-v1",
    )


def policy() -> GatewayPolicy:
    return GatewayPolicy(
        attempt_timeout_ms=2_000,
        total_timeout_ms=8_000,
        max_attempts_per_route=2,
        max_prompt_characters=32_000,
        max_output_tokens=2_048,
        max_response_characters=64_000,
        circuit_failure_threshold=3,
        circuit_recovery_ms=30_000,
        rule_degradation_message="当前模型暂不可用, 请稍后重试",
        max_estimated_cost_microunits=5_000_000,
    )


def routes() -> tuple[RuntimeRouteDraft, ...]:
    return (
        RuntimeRouteDraft(
            PRIMARY_ID,
            1,
            "synthetic-primary",
            frozenset({"generation", "streaming"}),
            20_000_000,
            40_000_000,
        ),
        RuntimeRouteDraft(
            BACKUP_ID,
            2,
            "synthetic-backup",
            frozenset({"generation"}),
            10_000_000,
            20_000_000,
        ),
    )


def create(service: AiRuntimeConfigurationService, name: str) -> AiRuntimeConfigVersion:
    return service.create(
        context(),
        display_name=name,
        system_prompt_template="你是只基于授权证据回答的合成助手。",
        components=components(),
        policy=policy(),
        routes=routes(),
    )


def test_runtime_versions_are_immutable_hashes_and_pointer_activation_supports_rollback() -> None:
    unit_of_work = MemoryUnitOfWork()
    service = AiRuntimeConfigurationService(unit_of_work)
    first = create(service, "首版运行配置")
    second = create(service, "第二版运行配置")

    assert first.version_number == 1
    assert second.version_number == 2
    assert len(first.content_hash) == 64
    assert len(first.system_prompt_hash) == 64
    assert [route.priority for route in first.routes] == [1, 2]
    assert first.routes[0].provider_configuration_version == 4
    first_publication = service.activate(context(), first.runtime_config_version_id)
    second_publication = service.activate(context(), second.runtime_config_version_id)
    rollback = service.activate(context(), first.runtime_config_version_id)
    assert (first_publication.generation, second_publication.generation, rollback.generation) == (
        1,
        2,
        3,
    )
    assert service.current_configuration(context()) == first

    # 后续供应商版本变化不会改写历史快照，实际调用仍会在边缘重新复核最新供应商事实。
    unit_of_work.runtime_configs.providers[PRIMARY_ID] = replace(
        unit_of_work.runtime_configs.providers[PRIMARY_ID], version=5, status="disabled"
    )
    assert first.routes[0].provider_configuration_version == 4
    assert unit_of_work.audit.actions == [
        "ai_runtime_config.created",
        "ai_runtime_config.created",
        "ai_runtime_config.activated",
        "ai_runtime_config.activated",
        "ai_runtime_config.activated",
    ]


def test_duplicate_invalid_and_unapproved_routes_are_rejected() -> None:
    unit_of_work = MemoryUnitOfWork()
    service = AiRuntimeConfigurationService(unit_of_work)
    create(service, "重复配置")
    with pytest.raises(AiRuntimeConfigConflictError):
        create(service, "重复配置")

    invalid_routes = (replace(routes()[0], priority=2), routes()[1])
    with pytest.raises(AiRuntimeConfigInvalidError):
        service.create(
            context(),
            display_name="优先级无效",
            system_prompt_template="合成提示",
            components=components(),
            policy=policy(),
            routes=invalid_routes,
        )

    unit_of_work.runtime_configs.providers[PRIMARY_ID] = replace(
        unit_of_work.runtime_configs.providers[PRIMARY_ID], status="draft"
    )
    with pytest.raises(AiRuntimeConfigInvalidError):
        service.create(
            context(),
            display_name="供应商未激活",
            system_prompt_template="合成提示",
            components=components(),
            policy=policy(),
            routes=(routes()[0],),
        )


def test_non_platform_administrator_cannot_read_or_create_runtime_configurations() -> None:
    unit_of_work = MemoryUnitOfWork()
    service = AiRuntimeConfigurationService(unit_of_work)

    with pytest.raises(PlatformAdministratorRequiredError):
        service.list_configurations(context(MEMBER_ID))
    with pytest.raises(PlatformAdministratorRequiredError):
        service.create(
            context(MEMBER_ID),
            display_name="越权配置",
            system_prompt_template="合成提示",
            components=components(),
            policy=policy(),
            routes=routes(),
        )
