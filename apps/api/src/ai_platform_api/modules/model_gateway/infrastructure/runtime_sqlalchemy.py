"""实现运行配置版本、发布指针和模型调用记录的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.model_gateway.domain.configuration import (
    ModelProviderConfiguration,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    GatewayPolicy,
    ModelAttempt,
    ModelCapability,
    ModelRequest,
    ProviderLocation,
)
from ai_platform_api.modules.model_gateway.domain.runtime import (
    AiRuntimeConfigPublication,
    AiRuntimeConfigVersion,
    RuntimeComponentVersions,
    RuntimeConfigurationUnitOfWork,
    RuntimeInvocationOutcome,
    RuntimeRouteSnapshot,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigConflictError,
    ModelInvocationConflictError,
)
from ai_platform_api.modules.model_gateway.infrastructure.configuration_sqlalchemy import (
    SqlAlchemyModelProviderRepository,
    SqlAlchemyPlatformAuditWriter,
)
from ai_platform_api.persistence.tables import (
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    ai_runtime_model_routes,
    model_invocation_attempts,
    model_invocations,
    platform_administrators,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyRuntimeConfigurationRepository:
    """维护平台级不可变运行配置版本和唯一当前发布指针。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._providers = SqlAlchemyModelProviderRepository(session)

    def is_platform_administrator(self, account_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(platform_administrators)
                .where(
                    platform_administrators.c.account_id == account_id,
                    platform_administrators.c.status == "active",
                )
            )
        )

    def list_configurations(self) -> tuple[AiRuntimeConfigVersion, ...]:
        rows = self._session.execute(
            select(ai_runtime_config_versions).order_by(
                ai_runtime_config_versions.c.version_number.desc()
            )
        )
        return tuple(self._configuration(row) for row in rows)

    def get_configuration(
        self,
        runtime_config_version_id: UUID,
        *,
        for_update: bool = False,
    ) -> AiRuntimeConfigVersion | None:
        statement = select(ai_runtime_config_versions).where(
            ai_runtime_config_versions.c.runtime_config_version_id == runtime_config_version_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return self._configuration(row) if row is not None else None

    def get_current_publication(
        self, *, for_update: bool = False
    ) -> AiRuntimeConfigPublication | None:
        statement = select(ai_runtime_config_publication).where(
            ai_runtime_config_publication.c.publication_key == "current"
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return AiRuntimeConfigPublication(
            row.runtime_config_version_id,
            row.generation,
            row.published_by_account_id,
            row.published_at,
        )

    def get_provider_configuration(self, provider_id: UUID) -> ModelProviderConfiguration | None:
        return self._providers.get_configuration(provider_id)

    def next_version_number(self) -> int:
        # 空表无法通过行锁串行化，固定 advisory lock 保证并发创建仍得到唯一递增版本。
        self._session.execute(text("SELECT pg_advisory_xact_lock(71610406)"))
        current = self._session.scalar(
            select(func.max(ai_runtime_config_versions.c.version_number))
        )
        return int(current or 0) + 1

    def add_configuration(self, configuration: AiRuntimeConfigVersion) -> None:
        try:
            self._session.execute(
                insert(ai_runtime_config_versions).values(
                    runtime_config_version_id=configuration.runtime_config_version_id,
                    version_number=configuration.version_number,
                    display_name=configuration.display_name,
                    content_hash=configuration.content_hash,
                    system_prompt_template=configuration.system_prompt_template,
                    system_prompt_hash=configuration.system_prompt_hash,
                    component_versions=asdict(configuration.components),
                    **_policy_values(configuration.policy),
                    created_by_account_id=configuration.created_by_account_id,
                    created_at=configuration.created_at,
                )
            )
            self._session.execute(
                insert(ai_runtime_model_routes),
                [
                    _route_values(configuration.runtime_config_version_id, route)
                    for route in configuration.routes
                ],
            )
        except IntegrityError as error:
            raise AiRuntimeConfigConflictError from error

    def publish(self, publication: AiRuntimeConfigPublication) -> None:
        statement = postgresql_insert(ai_runtime_config_publication).values(
            publication_key="current",
            runtime_config_version_id=publication.runtime_config_version_id,
            generation=publication.generation,
            published_by_account_id=publication.published_by_account_id,
            published_at=publication.published_at,
        )
        if publication.generation == 1:
            statement = statement.on_conflict_do_nothing(
                index_elements=[ai_runtime_config_publication.c.publication_key]
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=[ai_runtime_config_publication.c.publication_key],
                set_={
                    "runtime_config_version_id": publication.runtime_config_version_id,
                    "generation": publication.generation,
                    "published_by_account_id": publication.published_by_account_id,
                    "published_at": publication.published_at,
                },
                where=(ai_runtime_config_publication.c.generation == publication.generation - 1),
            )
        published_key = self._session.scalar(
            statement.returning(ai_runtime_config_publication.c.publication_key)
        )
        if published_key != "current":
            raise AiRuntimeConfigConflictError

    def _configuration(self, row: Row[Any]) -> AiRuntimeConfigVersion:
        route_rows = self._session.execute(
            select(ai_runtime_model_routes)
            .where(
                ai_runtime_model_routes.c.runtime_config_version_id == row.runtime_config_version_id
            )
            .order_by(ai_runtime_model_routes.c.priority)
        )
        return AiRuntimeConfigVersion(
            runtime_config_version_id=row.runtime_config_version_id,
            version_number=row.version_number,
            display_name=row.display_name,
            content_hash=row.content_hash,
            system_prompt_template=row.system_prompt_template,
            system_prompt_hash=row.system_prompt_hash,
            components=_runtime_component_versions(row.component_versions),
            policy=_policy(row),
            routes=tuple(_route(route_row) for route_row in route_rows),
            created_by_account_id=row.created_by_account_id,
            created_at=row.created_at,
        )


class SqlAlchemyRuntimeConfigurationUnitOfWork(RuntimeConfigurationUnitOfWork):
    """运行快照、发布指针和平台审计始终在同一事务中提交。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyRuntimeConfigurationRepository,
                SqlAlchemyPlatformAuditWriter,
            ]
            | None
        ] = ContextVar("runtime_configuration_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyRuntimeConfigurationUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Runtime Configuration Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyRuntimeConfigurationRepository(session),
                SqlAlchemyPlatformAuditWriter(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def runtime_configs(self) -> SqlAlchemyRuntimeConfigurationRepository:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Runtime Configuration Unit of Work 尚未进入事务范围")
        return state[1]

    @property
    def audit(self) -> SqlAlchemyPlatformAuditWriter:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Runtime Configuration Unit of Work 尚未进入事务范围")
        return state[2]

    def commit(self) -> None:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Runtime Configuration Unit of Work 尚未进入事务范围")
        state[0].commit()


class SqlAlchemyRuntimeConfigurationReader:
    """按冻结标识读取运行快照，配置发布指针变化不会影响在途 Run。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def get(self, runtime_config_version_id: UUID) -> AiRuntimeConfigVersion | None:
        with self._session_factory() as session:
            repository = SqlAlchemyRuntimeConfigurationRepository(session)
            return repository.get_configuration(runtime_config_version_id)


class SqlAlchemyRuntimeInvocationStore:
    """外部调用前占位，完成后以短事务固化尝试和计量，避免长事务跨网络。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def reserve(self, request: ModelRequest, runtime_config_version_id: UUID) -> None:
        try:
            with self._session_factory() as session, session.begin():
                session.execute(
                    insert(model_invocations).values(
                        invocation_id=request.invocation_id,
                        workspace_id=request.workspace_id,
                        runtime_config_version_id=runtime_config_version_id,
                        task_type=request.task_type,
                        security_level=request.security_level,
                        status="running",
                        trace_id=request.trace_id,
                        traceparent=request.traceparent,
                        external_data_allowed=request.external_data_allowed,
                        requested_max_output_tokens=request.max_output_tokens,
                        selected_route_id=None,
                        selected_provider_id=None,
                        selected_model_id=None,
                        input_tokens=0,
                        output_tokens=0,
                        estimated_cost_microunits=0,
                        currency="CNY",
                        finish_reason=None,
                        degradation_reason=None,
                        error_code=None,
                        started_at=datetime.now(UTC),
                        completed_at=None,
                    )
                )
        except IntegrityError as error:
            raise ModelInvocationConflictError from error

    def complete(self, outcome: RuntimeInvocationOutcome) -> None:
        # 1. 从成功尝试和最终结果生成稳定汇总值，失败调用仍记录零用量与错误码。
        selected_attempt = next(
            (attempt for attempt in reversed(outcome.attempts) if attempt.status == "succeeded"),
            None,
        )
        result = outcome.result
        values: dict[str, object] = {
            "status": outcome.status,
            "selected_route_id": (
                UUID(selected_attempt.route_id) if selected_attempt is not None else None
            ),
            "selected_provider_id": (
                UUID(result.provider_id) if result is not None and result.provider_id else None
            ),
            "selected_model_id": result.model_id if result is not None else None,
            "input_tokens": result.usage.input_tokens if result is not None else 0,
            "output_tokens": result.usage.output_tokens if result is not None else 0,
            "estimated_cost_microunits": (
                result.estimated_cost_microunits if result is not None else 0
            ),
            "currency": result.currency if result is not None else "CNY",
            "finish_reason": result.finish_reason if result is not None else None,
            "degradation_reason": result.degradation_reason if result is not None else None,
            "error_code": outcome.error_code,
            "completed_at": outcome.completed_at,
        }
        # 2. 只允许 running 状态完成一次，并与全部尝试明细在同一事务中落库。
        try:
            with self._session_factory() as session, session.begin():
                update_result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(model_invocations)
                        .where(
                            model_invocations.c.invocation_id == outcome.request.invocation_id,
                            model_invocations.c.runtime_config_version_id
                            == outcome.runtime_config_version_id,
                            model_invocations.c.status == "running",
                        )
                        .values(**values)
                    ),
                )
                if update_result.rowcount != 1:
                    raise ModelInvocationConflictError
                if outcome.attempts:
                    session.execute(
                        insert(model_invocation_attempts),
                        [
                            _attempt_values(outcome, attempt_index, attempt)
                            for attempt_index, attempt in enumerate(outcome.attempts, start=1)
                        ],
                    )
        except IntegrityError as error:
            raise ModelInvocationConflictError from error


def _policy(row: Row[Any]) -> GatewayPolicy:
    return GatewayPolicy(
        attempt_timeout_ms=row.attempt_timeout_ms,
        total_timeout_ms=row.total_timeout_ms,
        max_attempts_per_route=row.max_attempts_per_route,
        max_prompt_characters=row.max_prompt_characters,
        max_output_tokens=row.max_output_tokens,
        max_response_characters=row.max_response_characters,
        circuit_failure_threshold=row.circuit_failure_threshold,
        circuit_recovery_ms=row.circuit_recovery_ms,
        rule_degradation_message=row.rule_degradation_message,
        max_estimated_cost_microunits=row.max_estimated_cost_microunits,
    )


def _runtime_component_versions(value: object) -> RuntimeComponentVersions:
    components = cast("dict[str, str]", value)
    # 早期本地快照没有冻结后置接口版本；使用显式未知值保真，避免读取列表时伪造具体版本。
    legacy_interfaces = {
        "data_source_interface": "legacy-unversioned",
        "relevance_grader_interface": "legacy-unversioned",
        "multimodal_router_interface": "legacy-unversioned",
    }
    return RuntimeComponentVersions(**(legacy_interfaces | components))


def _policy_values(policy: GatewayPolicy) -> dict[str, object]:
    return asdict(policy)


def _route(row: Row[Any]) -> RuntimeRouteSnapshot:
    return RuntimeRouteSnapshot(
        route_id=row.route_id,
        provider_id=row.provider_id,
        provider_configuration_version=row.provider_configuration_version,
        priority=row.priority,
        model_id=row.model_id,
        location=cast("ProviderLocation", row.location),
        capabilities=frozenset(cast("list[ModelCapability]", row.capabilities)),
        input_price_microunits_per_million_tokens=(row.input_price_microunits_per_million_tokens),
        output_price_microunits_per_million_tokens=(row.output_price_microunits_per_million_tokens),
        currency="CNY",
    )


def _route_values(
    runtime_config_version_id: UUID,
    route: RuntimeRouteSnapshot,
) -> dict[str, object]:
    return {
        "route_id": route.route_id,
        "runtime_config_version_id": runtime_config_version_id,
        "provider_id": route.provider_id,
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


def _attempt_values(
    outcome: RuntimeInvocationOutcome,
    attempt_index: int,
    attempt: ModelAttempt,
) -> dict[str, object]:
    usage = attempt.usage
    return {
        "invocation_id": outcome.request.invocation_id,
        "attempt_index": attempt_index,
        "route_id": UUID(attempt.route_id),
        "provider_id": UUID(attempt.provider_id),
        "model_id": attempt.model_id,
        "credential_version": outcome.credential_versions.get(attempt.provider_id),
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "duration_ms": attempt.duration_ms,
        "failure_kind": attempt.failure_kind,
        "provider_request_id": attempt.provider_request_id,
        "input_tokens": usage.input_tokens if usage is not None else None,
        "output_tokens": usage.output_tokens if usage is not None else None,
        "estimated_cost_microunits": attempt.estimated_cost_microunits,
        "trace_id": attempt.trace_id,
        "traceparent": attempt.traceparent,
    }
