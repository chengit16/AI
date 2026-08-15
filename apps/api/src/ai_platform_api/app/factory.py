"""创建 FastAPI 应用并集中注册中间件、Router 与生命周期门禁。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ai_platform_backend.observability import ObservabilityRuntime
from fastapi import FastAPI

from ai_platform_api.app.dependencies import (
    ApplicationContainer,
    build_application_container,
)
from ai_platform_api.app.errors import register_error_handlers
from ai_platform_api.app.trace_middleware import TraceContextMiddleware
from ai_platform_api.common.api_errors import ErrorResponse
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.assistant.api.routes import router as assistant_router
from ai_platform_api.modules.authorization.api.routes import (
    menu_router,
)
from ai_platform_api.modules.authorization.api.routes import (
    router as authorization_router,
)
from ai_platform_api.modules.identity.api.enterprise_routes import router as workspace_router
from ai_platform_api.modules.identity.api.entitlement_routes import router as entitlement_router
from ai_platform_api.modules.identity.api.organization_routes import router as organization_router
from ai_platform_api.modules.identity.api.role_routes import router as role_router
from ai_platform_api.modules.identity.api.routes import router as identity_router
from ai_platform_api.modules.integration.api.routes import router as integration_operations_router
from ai_platform_api.modules.knowledge.api.routes import router as knowledge_router
from ai_platform_api.modules.model_gateway.api.routes import (
    router as model_provider_router,
)
from ai_platform_api.modules.model_gateway.api.routes import (
    runtime_router as ai_runtime_router,
)
from ai_platform_api.modules.system.api.health import router as health_router
from ai_platform_api.modules.system.api.observability import router as observability_router
from ai_platform_api.modules.workflow.api.approval_routes import router as approval_policy_router
from ai_platform_api.modules.workflow.api.approval_runtime_routes import (
    router as approval_instance_router,
)
from ai_platform_api.modules.workflow.api.routes import router as workflow_router


def create_app(
    settings: Settings | None = None,
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """创建 FastAPI 应用并安装路由、中间件和统一错误映射。"""

    # 1. 配置与容器必须来自同一启动快照，避免服务读取到彼此矛盾的运行参数。
    resolved_settings = settings or get_settings()
    dependencies = container or build_application_container(resolved_settings)
    if dependencies.settings != resolved_settings:
        raise ValueError("应用配置与依赖容器配置不一致")

    observability = ObservabilityRuntime(
        service_name=resolved_settings.app_name,
        environment=resolved_settings.environment,
        field_registry_path=resolved_settings.observability_field_registry_path,
        otlp_endpoint=resolved_settings.observability_otlp_endpoint,
        otlp_timeout_seconds=resolved_settings.observability_otlp_timeout_seconds,
        metrics_process_prefix="api",
        configure_library_logging=resolved_settings.observability_safe_library_logging,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            dependencies.close()
            observability.shutdown()

    # 2. 应用生命周期统一接管容器关闭，并把领域能力显式暴露给依赖解析函数。
    application = FastAPI(
        title="AI 智能平台 API",
        summary="个人空间与企业空间共用的平台服务接口",
        version=resolved_settings.version,
        openapi_version="3.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
        responses={500: {"model": ErrorResponse, "description": "平台内部错误"}},
    )
    application.state.container = dependencies
    application.state.observability = observability
    application.state.authentication_service = dependencies.authentication
    application.state.registration_service = dependencies.registration
    application.state.enterprise_workspace_service = dependencies.enterprise_workspaces
    application.state.entitlement_service = dependencies.entitlements
    application.state.integration_operations_service = dependencies.integration_operations
    application.state.organization_service = dependencies.organization
    application.state.role_service = dependencies.roles
    application.state.resource_registry = dependencies.resource_registry
    application.state.field_projection_service = dependencies.field_projection
    application.state.policy_decision_point = dependencies.policy
    application.state.role_permission_service = dependencies.role_permissions
    application.state.menu_configuration_service = dependencies.menu_configuration
    application.state.menu_release_service = dependencies.menu_releases
    application.state.knowledge_fact_service = dependencies.knowledge_facts
    application.state.knowledge_upload_service = dependencies.knowledge_uploads
    application.state.knowledge_management_service = dependencies.knowledge_management
    application.state.model_provider_configuration_service = (
        dependencies.model_provider_configurations
    )
    application.state.ai_runtime_configuration_service = dependencies.ai_runtime_configurations
    application.state.model_runtime_service = dependencies.model_runtime
    application.state.assistant_conversation_service = dependencies.assistant_conversations
    application.state.assistant_run_executor = dependencies.assistant_run_executor
    application.state.assistant_source_service = dependencies.assistant_sources
    application.state.streaming_service = dependencies.streaming
    application.state.retrieval_planning_service = dependencies.retrieval_planning
    application.state.retrieval_evidence_service = dependencies.retrieval_evidence
    application.state.workflow_definition_service = dependencies.workflows
    application.state.workflow_run_executor = dependencies.workflow_run_executor
    application.state.approval_policy_service = dependencies.approval_policies
    application.state.approval_instance_service = dependencies.approval_instances
    # 3. 中间件、错误映射和 Router 在状态装配后注册，所有业务入口共享同一安全边界。
    application.dependency_overrides[get_settings] = lambda: resolved_settings
    application.add_middleware(TraceContextMiddleware, observability=observability)
    register_error_handlers(application, dependencies.errors)
    application.include_router(health_router, prefix="/api/v1")
    application.include_router(observability_router, prefix="/api/v1")
    application.include_router(identity_router, prefix="/api/v1")
    application.include_router(workspace_router, prefix="/api/v1")
    application.include_router(entitlement_router, prefix="/api/v1")
    application.include_router(integration_operations_router, prefix="/api/v1")
    application.include_router(organization_router, prefix="/api/v1")
    application.include_router(role_router, prefix="/api/v1")
    application.include_router(authorization_router, prefix="/api/v1")
    application.include_router(menu_router, prefix="/api/v1")
    application.include_router(knowledge_router, prefix="/api/v1")
    application.include_router(model_provider_router, prefix="/api/v1")
    application.include_router(ai_runtime_router, prefix="/api/v1")
    application.include_router(assistant_router, prefix="/api/v1")
    application.include_router(workflow_router, prefix="/api/v1")
    application.include_router(approval_policy_router, prefix="/api/v1")
    application.include_router(approval_instance_router, prefix="/api/v1")
    return application
