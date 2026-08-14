from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.modules.model_gateway.api.dependencies import (
    model_provider_configuration_service,
    trusted_platform_context,
)
from ai_platform_api.modules.model_gateway.api.schemas import (
    Capability,
    CreateModelProviderRequest,
    ModelProviderConfigurationListResponse,
    ModelProviderConfigurationResponse,
    ReviewModelProviderDataPolicyRequest,
    RotateModelProviderCredentialRequest,
)
from ai_platform_api.modules.model_gateway.application.configurations import (
    ModelProviderConfiguration,
    ModelProviderConfigurationService,
)

router = APIRouter(prefix="/platform/model-providers", tags=["平台模型供应商"])


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
    response.headers["Cache-Control"] = "no-store"
    return _response(
        service.create(
            context,
            provider_key=body.provider_key,
            display_name=body.display_name,
            adapter_kind=body.adapter_kind,
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
    response.headers["Cache-Control"] = "no-store"
    return _response(service.disable(context, provider_id))


def _response(configuration: ModelProviderConfiguration) -> ModelProviderConfigurationResponse:
    return ModelProviderConfigurationResponse(
        provider_id=configuration.provider_id,
        provider_key=configuration.provider_key,
        display_name=configuration.display_name,
        adapter_kind=configuration.adapter_kind,
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
