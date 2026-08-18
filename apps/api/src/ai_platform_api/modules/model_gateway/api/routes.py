"""映射模型供应商治理、能力探测和运行配置发布 HTTP 协议。"""

from dataclasses import asdict
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.modules.model_gateway.api.dependencies import (
    ai_runtime_configuration_service,
    model_provider_configuration_service,
    trusted_platform_context,
)
from ai_platform_api.modules.model_gateway.api.schemas import (
    AiRuntimeConfigListResponse,
    AiRuntimeConfigPublicationResponse,
    AiRuntimeConfigResponse,
    Capability,
    CreateAiRuntimeConfigRequest,
    CreateModelProviderRequest,
    CurrentAiRuntimeConfigResponse,
    GatewayPolicySchema,
    ModelProviderConfigurationListResponse,
    ModelProviderConfigurationResponse,
    ReviewModelProviderDataPolicyRequest,
    RotateModelProviderCredentialRequest,
    RuntimeComponentVersionsSchema,
    RuntimeRouteResponse,
)
from ai_platform_api.modules.model_gateway.application.configurations import (
    ModelProviderConfiguration,
    ModelProviderConfigurationService,
)
from ai_platform_api.modules.model_gateway.application.runtime_configurations import (
    AiRuntimeConfigurationService,
    AiRuntimeConfigVersion,
    GatewayPolicy,
    RuntimeComponentVersions,
    RuntimeRouteDraft,
)

router = APIRouter(prefix="/platform/model-providers", tags=["平台模型供应商"])
runtime_router = APIRouter(prefix="/platform/ai-runtime-configs", tags=["平台 AI 运行配置"])


@router.get(
    "",
    response_model=ModelProviderConfigurationListResponse,
    operation_id="listPlatformModelProviders",
    responses=error_responses(401, 403, 422, 500),
)
def list_model_providers(
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        ModelProviderConfigurationService,
        Depends(model_provider_configuration_service),
    ],
) -> ModelProviderConfigurationListResponse:
    """列出模型供应商集合；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return ModelProviderConfigurationListResponse(
        items=[_response(item) for item in service.list_configurations(context)]
    )


@router.post(
    "",
    response_model=ModelProviderConfigurationResponse,
    operation_id="createPlatformModelProvider",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(401, 403, 409, 422, 500),
)
def create_model_provider(
    body: CreateModelProviderRequest,
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        ModelProviderConfigurationService,
        Depends(model_provider_configuration_service),
    ],
) -> ModelProviderConfigurationResponse:
    """创建模型供应商；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return _response(
        service.create(
            context,
            provider_key=body.provider_key,
            display_name=body.display_name,
            adapter_kind=body.adapter_kind,
            wire_api=body.wire_api,
            base_url=body.base_url,
            probe_model_id=body.probe_model_id,
            location=body.location,
            declared_capabilities=frozenset(body.declared_capabilities),
            api_key=body.api_key.get_secret_value(),
        )
    )


@router.post(
    "/{provider_id}/credentials/rotate",
    response_model=ModelProviderConfigurationResponse,
    operation_id="rotatePlatformModelProviderCredential",
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
def rotate_model_provider_credential(
    provider_id: UUID,
    body: RotateModelProviderCredentialRequest,
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        ModelProviderConfigurationService,
        Depends(model_provider_configuration_service),
    ],
) -> ModelProviderConfigurationResponse:
    """处理轮换模型供应商凭据；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return _response(
        service.rotate_credential(
            context,
            provider_id,
            api_key=body.api_key.get_secret_value(),
        )
    )


@router.post(
    "/{provider_id}/data-policy/review",
    response_model=ModelProviderConfigurationResponse,
    operation_id="reviewPlatformModelProviderDataPolicy",
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
def review_model_provider_data_policy(
    provider_id: UUID,
    body: ReviewModelProviderDataPolicyRequest,
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        ModelProviderConfigurationService,
        Depends(model_provider_configuration_service),
    ],
) -> ModelProviderConfigurationResponse:
    """审核模型供应商数据策略；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return _response(
        service.review_data_policy(
            context,
            provider_id,
            approved=body.approved,
            max_security_level=body.max_security_level,
            retention_days=body.retention_days,
            training_usage_allowed=body.training_usage_allowed,
            policy_url=str(body.policy_url) if body.policy_url is not None else None,
            policy_version=body.policy_version,
        )
    )


@router.post(
    "/{provider_id}/probe",
    response_model=ModelProviderConfigurationResponse,
    operation_id="probePlatformModelProviderCapabilities",
    responses=error_responses(401, 403, 404, 409, 422, 503, 500),
)
def probe_model_provider(
    provider_id: UUID,
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        ModelProviderConfigurationService,
        Depends(model_provider_configuration_service),
    ],
) -> ModelProviderConfigurationResponse:
    """探测模型供应商；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return _response(service.probe(context, provider_id))


@router.post(
    "/{provider_id}/activate",
    response_model=ModelProviderConfigurationResponse,
    operation_id="activatePlatformModelProvider",
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
def activate_model_provider(
    provider_id: UUID,
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        ModelProviderConfigurationService,
        Depends(model_provider_configuration_service),
    ],
) -> ModelProviderConfigurationResponse:
    """启用模型供应商；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return _response(service.activate(context, provider_id))


@router.post(
    "/{provider_id}/disable",
    response_model=ModelProviderConfigurationResponse,
    operation_id="disablePlatformModelProvider",
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
def disable_model_provider(
    provider_id: UUID,
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        ModelProviderConfigurationService,
        Depends(model_provider_configuration_service),
    ],
) -> ModelProviderConfigurationResponse:
    """停用模型供应商；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return _response(service.disable(context, provider_id))


def _response(configuration: ModelProviderConfiguration) -> ModelProviderConfigurationResponse:
    return ModelProviderConfigurationResponse(
        provider_id=configuration.provider_id,
        provider_key=configuration.provider_key,
        display_name=configuration.display_name,
        adapter_kind=configuration.adapter_kind,
        wire_api=configuration.wire_api,
        base_url=configuration.base_url,
        probe_model_id=configuration.probe_model_id,
        location=configuration.location,
        declared_capabilities=cast("list[Capability]", sorted(configuration.declared_capabilities)),
        policy_review_status=configuration.policy_review_status,
        max_security_level=configuration.max_security_level,
        retention_days=configuration.retention_days,
        training_usage_allowed=configuration.training_usage_allowed,
        policy_url=configuration.policy_url,
        policy_version=configuration.policy_version,
        policy_reviewed_at=configuration.policy_reviewed_at,
        probe_status=configuration.probe_status,
        probed_capabilities=cast("list[Capability]", sorted(configuration.probed_capabilities)),
        last_probe_error_code=configuration.last_probe_error_code,
        last_probed_at=configuration.last_probed_at,
        status=configuration.status,
        created_at=configuration.created_at,
        updated_at=configuration.updated_at,
        version=configuration.version,
    )


@runtime_router.get(
    "",
    response_model=AiRuntimeConfigListResponse,
    operation_id="listPlatformAiRuntimeConfigs",
    responses=error_responses(401, 403, 422, 500),
)
def list_ai_runtime_configs(
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        AiRuntimeConfigurationService,
        Depends(ai_runtime_configuration_service),
    ],
) -> AiRuntimeConfigListResponse:
    """列出AI运行时配置集合；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return AiRuntimeConfigListResponse(
        items=[_runtime_response(item) for item in service.list_configurations(context)]
    )


@runtime_router.get(
    "/current",
    response_model=CurrentAiRuntimeConfigResponse,
    operation_id="getCurrentPlatformAiRuntimeConfig",
    responses=error_responses(401, 403, 422, 500),
)
def get_current_ai_runtime_config(
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        AiRuntimeConfigurationService,
        Depends(ai_runtime_configuration_service),
    ],
) -> CurrentAiRuntimeConfigResponse:
    """获取当前AI运行时配置；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    current = service.current_configuration(context)
    return CurrentAiRuntimeConfigResponse(
        item=_runtime_response(current) if current is not None else None
    )


@runtime_router.post(
    "",
    response_model=AiRuntimeConfigResponse,
    operation_id="createPlatformAiRuntimeConfig",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(401, 403, 409, 422, 500),
)
def create_ai_runtime_config(
    body: CreateAiRuntimeConfigRequest,
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        AiRuntimeConfigurationService,
        Depends(ai_runtime_configuration_service),
    ],
) -> AiRuntimeConfigResponse:
    """创建AI运行时配置；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    return _runtime_response(
        service.create(
            context,
            display_name=body.display_name,
            system_prompt_template=body.system_prompt_template,
            components=RuntimeComponentVersions(**body.components.model_dump()),
            policy=GatewayPolicy(**body.policy.model_dump()),
            routes=tuple(
                RuntimeRouteDraft(
                    provider_id=item.provider_id,
                    priority=item.priority,
                    model_id=item.model_id,
                    capabilities=frozenset(item.capabilities),
                    input_price_microunits_per_million_tokens=(
                        item.input_price_microunits_per_million_tokens
                    ),
                    output_price_microunits_per_million_tokens=(
                        item.output_price_microunits_per_million_tokens
                    ),
                )
                for item in body.routes
            ),
        )
    )


@runtime_router.post(
    "/{runtime_config_version_id}/activate",
    response_model=AiRuntimeConfigPublicationResponse,
    operation_id="activatePlatformAiRuntimeConfig",
    responses=error_responses(401, 403, 404, 409, 422, 500),
)
def activate_ai_runtime_config(
    runtime_config_version_id: UUID,
    response: Response,
    context: Annotated[PlatformRequestContext, Depends(trusted_platform_context)],
    service: Annotated[
        AiRuntimeConfigurationService,
        Depends(ai_runtime_configuration_service),
    ],
) -> AiRuntimeConfigPublicationResponse:
    """启用AI运行时配置；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    response.headers["Cache-Control"] = "no-store"
    publication = service.activate(context, runtime_config_version_id)
    return AiRuntimeConfigPublicationResponse(**asdict(publication))


def _runtime_response(configuration: AiRuntimeConfigVersion) -> AiRuntimeConfigResponse:
    return AiRuntimeConfigResponse(
        runtime_config_version_id=configuration.runtime_config_version_id,
        version_number=configuration.version_number,
        display_name=configuration.display_name,
        content_hash=configuration.content_hash,
        system_prompt_template=configuration.system_prompt_template,
        system_prompt_hash=configuration.system_prompt_hash,
        components=RuntimeComponentVersionsSchema(**asdict(configuration.components)),
        policy=GatewayPolicySchema(**asdict(configuration.policy)),
        routes=[
            RuntimeRouteResponse(
                route_id=item.route_id,
                provider_id=item.provider_id,
                provider_configuration_version=item.provider_configuration_version,
                priority=item.priority,
                model_id=item.model_id,
                location=item.location,
                capabilities=cast("list[Capability]", sorted(item.capabilities)),
                input_price_microunits_per_million_tokens=(
                    item.input_price_microunits_per_million_tokens
                ),
                output_price_microunits_per_million_tokens=(
                    item.output_price_microunits_per_million_tokens
                ),
                currency=item.currency,
            )
            for item in configuration.routes
        ],
        created_by_account_id=configuration.created_by_account_id,
        created_at=configuration.created_at,
    )
