"""编排不可变 AI 运行配置版本创建、查询和原子发布事务。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.safety import RAG_SAFETY_VERSION

from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    PlatformAdministratorRequiredError,
)
from ai_platform_api.modules.model_gateway.domain.models import GatewayPolicy
from ai_platform_api.modules.model_gateway.domain.runtime import (
    AiRuntimeConfigPublication,
    AiRuntimeConfigVersion,
    RuntimeComponentVersions,
    RuntimeConfigurationUnitOfWork,
    RuntimeRouteDraft,
    RuntimeRouteSnapshot,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigInvalidError,
    AiRuntimeConfigNotFoundError,
)

VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SUPPORTED_CAPABILITIES: frozenset[str] = frozenset(
    {"generation", "streaming", "tools", "structured_output"}
)

__all__ = [
    "AiRuntimeConfigVersion",
    "AiRuntimeConfigurationService",
    "GatewayPolicy",
    "RuntimeComponentVersions",
    "RuntimeRouteDraft",
]


class AiRuntimeConfigurationService:
    """创建不可变 AI 运行快照，并以独立指针完成发布和回滚。"""

    def __init__(self, unit_of_work: RuntimeConfigurationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def list_configurations(
        self, context: PlatformRequestContext
    ) -> tuple[AiRuntimeConfigVersion, ...]:
        """仅向平台管理员列出不可变运行配置版本。"""

        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            return unit_of_work.runtime_configs.list_configurations()

    def current_configuration(
        self, context: PlatformRequestContext
    ) -> AiRuntimeConfigVersion | None:
        """读取当前发布指针及完整运行快照，未发布时返回明确空状态。"""

        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            publication = unit_of_work.runtime_configs.get_current_publication()
            if publication is None:
                return None
            return unit_of_work.runtime_configs.get_configuration(
                publication.runtime_config_version_id
            )

    def create(
        self,
        context: PlatformRequestContext,
        *,
        display_name: str,
        system_prompt_template: str,
        components: RuntimeComponentVersions,
        policy: GatewayPolicy,
        routes: tuple[RuntimeRouteDraft, ...],
    ) -> AiRuntimeConfigVersion:
        """冻结供应商版本、模型路由、系统提示词和组件版本为不可变配置。"""

        # 1. 在事务外校验配置结构和版本字符串，减少持锁时间。
        normalized_name = display_name.strip()
        normalized_prompt = system_prompt_template.strip()
        self._validate_snapshot(normalized_name, normalized_prompt, components, policy, routes)
        now = datetime.now(UTC)
        runtime_config_version_id = uuid4()

        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            # 2. 逐条解析活动供应商并冻结其配置版本、位置、能力和成本。
            snapshots: list[RuntimeRouteSnapshot] = []
            for route in sorted(routes, key=lambda item: item.priority):
                provider = unit_of_work.runtime_configs.get_provider_configuration(
                    route.provider_id
                )
                if (
                    provider is None
                    or provider.status != "active"
                    or not route.capabilities.issubset(provider.probed_capabilities)
                ):
                    raise AiRuntimeConfigInvalidError
                snapshots.append(
                    RuntimeRouteSnapshot(
                        route_id=uuid4(),
                        provider_id=provider.provider_id,
                        provider_configuration_version=provider.version,
                        priority=route.priority,
                        model_id=route.model_id.strip(),
                        location=provider.location,
                        capabilities=route.capabilities,
                        input_price_microunits_per_million_tokens=(
                            route.input_price_microunits_per_million_tokens
                        ),
                        output_price_microunits_per_million_tokens=(
                            route.output_price_microunits_per_million_tokens
                        ),
                    )
                )

            # 3. 对完整内容计算摘要并写入新版本，后续发布只移动指针不修改快照。
            version_number = unit_of_work.runtime_configs.next_version_number()
            system_prompt_hash = hashlib.sha256(normalized_prompt.encode()).hexdigest()
            configuration = AiRuntimeConfigVersion(
                runtime_config_version_id=runtime_config_version_id,
                version_number=version_number,
                display_name=normalized_name,
                content_hash=_content_hash(
                    normalized_name,
                    normalized_prompt,
                    components,
                    policy,
                    tuple(snapshots),
                ),
                system_prompt_template=normalized_prompt,
                system_prompt_hash=system_prompt_hash,
                components=components,
                policy=policy,
                routes=tuple(snapshots),
                created_by_account_id=context.account_id,
                created_at=now,
            )
            # 4. 配置版本和平台审计同事务提交。
            unit_of_work.runtime_configs.add_configuration(configuration)
            unit_of_work.audit.add(
                account_id=context.account_id,
                runtime_config_version_id=runtime_config_version_id,
                action="ai_runtime_config.created",
                request_id=context.request_id,
                trace_id=context.trace.trace_id,
                occurred_at=now,
                details={
                    "version_number": version_number,
                    "content_hash": configuration.content_hash,
                    "route_count": len(snapshots),
                },
            )
            unit_of_work.commit()
        return configuration

    def activate(
        self,
        context: PlatformRequestContext,
        runtime_config_version_id: UUID,
    ) -> AiRuntimeConfigPublication:
        """校验所有路由供应商可用后原子切换发布指针并递增代次。"""

        # 1. 锁定目标配置和当前发布指针，重复发布同一版本按幂等返回。
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            configuration = unit_of_work.runtime_configs.get_configuration(
                runtime_config_version_id,
                for_update=True,
            )
            if configuration is None:
                raise AiRuntimeConfigNotFoundError
            current = unit_of_work.runtime_configs.get_current_publication(for_update=True)
            if (
                current is not None
                and current.runtime_config_version_id == runtime_config_version_id
            ):
                return current
            # 2. 发布代次单调递增，作为进程内外缓存失效的稳定版本。
            publication = AiRuntimeConfigPublication(
                runtime_config_version_id=runtime_config_version_id,
                generation=(current.generation + 1) if current is not None else 1,
                published_by_account_id=context.account_id,
                published_at=now,
            )
            # 3. 当前指针和平台审计同事务提交，调用路径不会看到无审计的配置切换。
            unit_of_work.runtime_configs.publish(publication)
            unit_of_work.audit.add(
                account_id=context.account_id,
                runtime_config_version_id=runtime_config_version_id,
                action="ai_runtime_config.activated",
                request_id=context.request_id,
                trace_id=context.trace.trace_id,
                occurred_at=now,
                details={
                    "generation": publication.generation,
                    "previous_runtime_config_version_id": (
                        str(current.runtime_config_version_id) if current is not None else "none"
                    ),
                },
            )
            unit_of_work.commit()
        return publication

    @staticmethod
    def _require_administrator(
        unit_of_work: RuntimeConfigurationUnitOfWork,
        account_id: UUID,
    ) -> None:
        if not unit_of_work.runtime_configs.is_platform_administrator(account_id):
            raise PlatformAdministratorRequiredError

    @staticmethod
    def _validate_snapshot(
        display_name: str,
        system_prompt_template: str,
        components: RuntimeComponentVersions,
        policy: GatewayPolicy,
        routes: tuple[RuntimeRouteDraft, ...],
    ) -> None:
        # 1. 校验全局结构、连续优先级、组件版本和网关安全上限。
        version_values = asdict(components).values()
        priorities = sorted(route.priority for route in routes)
        route_keys = {(route.provider_id, route.model_id.strip()) for route in routes}
        # 安全门版本必须与当前后端实现一致，旧配置不能绕过最新的模型前置防护。
        if (
            not display_name
            or len(display_name) > 120
            or not system_prompt_template
            or len(system_prompt_template) > 16_000
            or any(
                not isinstance(value, str) or VERSION_PATTERN.fullmatch(value) is None
                for value in version_values
            )
            or components.safety != RAG_SAFETY_VERSION
            or not 1 <= len(routes) <= 8
            or priorities != list(range(1, len(routes) + 1))
            or len(route_keys) != len(routes)
            or policy.attempt_timeout_ms > 120_000
            or policy.total_timeout_ms > 300_000
            or policy.max_attempts_per_route > 5
            or policy.max_prompt_characters > 2_000_000
            or policy.max_output_tokens > 65_536
            or policy.max_response_characters > 4_000_000
            or policy.circuit_failure_threshold > 20
            or policy.circuit_recovery_ms > 3_600_000
            or policy.max_estimated_cost_microunits > 1_000_000_000_000
        ):
            raise AiRuntimeConfigInvalidError
        # 2. 每条路由必须具备生成能力、合法模型标识和非负且有上限的成本。
        for route in routes:
            if (
                route.priority < 1
                or not route.model_id.strip()
                or len(route.model_id.strip()) > 255
                or "generation" not in route.capabilities
                or not route.capabilities
                or not set(route.capabilities).issubset(SUPPORTED_CAPABILITIES)
                or min(
                    route.input_price_microunits_per_million_tokens,
                    route.output_price_microunits_per_million_tokens,
                )
                < 0
                or max(
                    route.input_price_microunits_per_million_tokens,
                    route.output_price_microunits_per_million_tokens,
                )
                > 1_000_000_000_000
            ):
                raise AiRuntimeConfigInvalidError


def _content_hash(
    display_name: str,
    system_prompt_template: str,
    components: RuntimeComponentVersions,
    policy: GatewayPolicy,
    routes: tuple[RuntimeRouteSnapshot, ...],
) -> str:
    document = {
        "display_name": display_name,
        "system_prompt_template": system_prompt_template,
        "components": asdict(components),
        "policy": asdict(policy),
        "routes": [
            {
                "provider_id": str(route.provider_id),
                "provider_configuration_version": route.provider_configuration_version,
                "priority": route.priority,
                "model_id": route.model_id,
                "location": route.location,
                "capabilities": sorted(route.capabilities),
                "input_price_microunits_per_million_tokens": (
                    route.input_price_microunits_per_million_tokens
                ),
                "output_price_microunits_per_million_tokens": (
                    route.output_price_microunits_per_million_tokens
                ),
                "currency": route.currency,
            }
            for route in routes
        ],
    }
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
