"""装配 FastAPI 请求依赖、可信上下文和各业务模块服务。"""

from dataclasses import dataclass, field
from pathlib import Path

from ai_platform_backend.indexing.embeddings import DeterministicHashEmbeddingAdapter
from ai_platform_backend.safety import RagSafetyGate

from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.app.local_mock import (
    LOCAL_MOCK_PROVIDER_ID,
    LOCAL_MOCK_PROVIDER_KEY,
    LocalMockRuntimeBootstrap,
)
from ai_platform_api.config import Settings
from ai_platform_api.modules.assistant.application.runner import AssistantRunExecutor
from ai_platform_api.modules.assistant.application.service import AssistantConversationService
from ai_platform_api.modules.assistant.application.sources import AssistantSourceService
from ai_platform_api.modules.assistant.infrastructure.sqlalchemy import (
    SqlAlchemyAssistantUnitOfWork,
)
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.authorization.application.grants import RolePermissionService
from ai_platform_api.modules.authorization.application.menu_releases import MenuReleaseService
from ai_platform_api.modules.authorization.application.menus import MenuConfigurationService
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.fields import FieldPolicyRegistry
from ai_platform_api.modules.authorization.domain.policy import PolicyDecisionPoint
from ai_platform_api.modules.authorization.domain.resources import ResourceRegistry
from ai_platform_api.modules.authorization.infrastructure.menu_sqlalchemy import (
    SqlAlchemyMenuConfigurationUnitOfWork,
)
from ai_platform_api.modules.authorization.infrastructure.sqlalchemy import (
    SqlAlchemyPolicyGrantRepository,
    SqlAlchemyRolePermissionUnitOfWork,
)
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementAccessReader,
    SqlAlchemyEntitlementRepository,
    SqlAlchemyEntitlementUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.role_cache import ValkeyRoleResolutionCache
from ai_platform_api.modules.identity.infrastructure.roles_sqlalchemy import (
    SqlAlchemyRoleUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.security import (
    Argon2idPasswordAdapter,
    EnvelopeSecretCipher,
    MasterKeyFile,
    Sha256SecretDigester,
)
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyIdentityUnitOfWork,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.knowledge.application.facts import KnowledgeFactService
from ai_platform_api.modules.knowledge.application.management import KnowledgeManagementService
from ai_platform_api.modules.knowledge.application.uploads import KnowledgeUploadService
from ai_platform_api.modules.knowledge.infrastructure.object_storage import MinioObjectStorage
from ai_platform_api.modules.knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyKnowledgeUnitOfWork,
)
from ai_platform_api.modules.knowledge.infrastructure.upload_security import (
    BoundedUploadInspector,
    DeterministicUploadScanner,
)
from ai_platform_api.modules.model_gateway.application.configurations import (
    ModelProviderConfigurationService,
)
from ai_platform_api.modules.model_gateway.application.context import (
    AuthorizedModelContextBuilder,
)
from ai_platform_api.modules.model_gateway.application.runtime_configurations import (
    AiRuntimeConfigurationService,
)
from ai_platform_api.modules.model_gateway.application.runtime_gateway import (
    RuntimeModelGatewayService,
)
from ai_platform_api.modules.model_gateway.infrastructure.configuration_sqlalchemy import (
    SqlAlchemyModelProviderUnitOfWork,
)
from ai_platform_api.modules.model_gateway.infrastructure.local_mock import (
    LocalMockRuntimeProviderFactory,
)
from ai_platform_api.modules.model_gateway.infrastructure.provider_http import (
    OpenAiCompatibleCapabilityProbe,
    OpenAiCompatibleRuntimeProviderFactory,
    StrictProviderBaseUrlPolicy,
)
from ai_platform_api.modules.model_gateway.infrastructure.runtime_sqlalchemy import (
    SqlAlchemyRuntimeConfigurationReader,
    SqlAlchemyRuntimeConfigurationUnitOfWork,
    SqlAlchemyRuntimeInvocationStore,
)
from ai_platform_api.modules.release.application.startup import verify_release_compatibility
from ai_platform_api.modules.retrieval.application.evidence import RetrievalEvidenceService
from ai_platform_api.modules.retrieval.application.planning import (
    BoundedRetrievalPlanningService,
)
from ai_platform_api.modules.retrieval.infrastructure.evidence_sqlalchemy import (
    SqlAlchemyEvidenceProcessingUnitOfWork,
)
from ai_platform_api.modules.retrieval.infrastructure.planning_sqlalchemy import (
    SqlAlchemyRetrievalPlanningUnitOfWork,
)
from ai_platform_api.modules.retrieval.infrastructure.reranking import (
    DeterministicLexicalReranker,
)
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService
from ai_platform_api.modules.streaming.domain.models import StreamPolicy
from ai_platform_api.modules.streaming.infrastructure.sqlalchemy import (
    SqlAlchemyStreamUnitOfWork,
)
from ai_platform_api.modules.workflow.application.service import WorkflowDefinitionService
from ai_platform_api.modules.workflow.infrastructure.sqlalchemy import (
    SqlAlchemyWorkflowUnitOfWork,
)
from ai_platform_api.persistence.database import PlatformDatabase


@dataclass(frozen=True)
class ApplicationContainer:
    """集中持有进程级依赖，业务模块只接收其实际需要的窄接口。"""

    settings: Settings
    database: PlatformDatabase
    errors: ErrorCatalog
    resource_registry: ResourceRegistry
    policy: PolicyDecisionPoint
    role_permissions: RolePermissionService
    authentication: AuthenticationService
    api_keys: ApiKeyService
    registration: RegistrationService
    enterprise_workspaces: EnterpriseWorkspaceService
    entitlements: EntitlementService
    organization: OrganizationService
    roles: RoleService
    role_cache: ValkeyRoleResolutionCache
    secret_cipher: EnvelopeSecretCipher
    sessions: ValkeySessionStore
    menu_configuration: MenuConfigurationService | None = None
    menu_releases: MenuReleaseService | None = None
    knowledge_facts: KnowledgeFactService | None = None
    knowledge_uploads: KnowledgeUploadService | None = None
    knowledge_management: KnowledgeManagementService | None = None
    model_provider_configurations: ModelProviderConfigurationService | None = None
    ai_runtime_configurations: AiRuntimeConfigurationService | None = None
    model_runtime: RuntimeModelGatewayService | None = None
    assistant_conversations: AssistantConversationService | None = None
    assistant_run_executor: AssistantRunExecutor | None = None
    assistant_sources: AssistantSourceService | None = None
    streaming: TransactionalStreamService | None = None
    retrieval_planning: BoundedRetrievalPlanningService | None = None
    retrieval_evidence: RetrievalEvidenceService | None = None
    workflows: WorkflowDefinitionService | None = None
    rag_safety: RagSafetyGate = field(default_factory=RagSafetyGate)
    field_policy_registry: FieldPolicyRegistry = field(
        default_factory=lambda: FieldPolicyRegistry(1, 1, ())
    )
    field_projection: FieldProjectionService = field(
        default_factory=lambda: FieldProjectionService(FieldPolicyRegistry(1, 1, ()))
    )

    def close(self) -> None:
        try:
            try:
                self.role_cache.close()
            finally:
                self.sessions.close()
        finally:
            self.database.close()


def build_application_container(settings: Settings) -> ApplicationContainer:
    """装配应用服务及基础设施适配器，集中维护进程级依赖生命周期。"""

    # 长函数保留原因: 进程级资源必须在一个组合根中显式建立所有权和逆序清理关系。
    # 1. 启动前先验证发布兼容性，再创建数据库、会话和角色缓存等进程级资源。
    verify_release_compatibility(
        settings.release_manifest_path,
        settings.compatibility_matrix_path,
    )
    database = PlatformDatabase.create(settings.database_url)
    sessions = ValkeySessionStore(settings.valkey_url)
    role_cache = ValkeyRoleResolutionCache(settings.valkey_url)
    reader = SqlAlchemyIdentityReader(database.sessions)
    entitlement_access = SqlAlchemyEntitlementAccessReader(database.sessions)
    digester = Sha256SecretDigester()
    passwords = Argon2idPasswordAdapter()
    # 2. 从冻结注册表装配授权与领域服务，所有服务共享同一数据库 SessionFactory。
    resource_registry = load_resource_registry(Path(settings.resource_registry_path))
    field_registry = load_field_policy_registry(Path(settings.field_policy_registry_path))
    field_projection = FieldProjectionService(field_registry)
    rag_safety = RagSafetyGate()
    policy_reader = SqlAlchemyPolicyGrantRepository(database.sessions)
    menu_configuration = MenuConfigurationService(
        resource_registry,
        SqlAlchemyMenuConfigurationUnitOfWork(database.sessions),
    )
    menu_releases = MenuReleaseService(
        resource_registry,
        SqlAlchemyMenuConfigurationUnitOfWork(database.sessions),
    )
    knowledge_facts = KnowledgeFactService(
        SqlAlchemyKnowledgeUnitOfWork(
            database.sessions,
            SqlAlchemyEntitlementRepository,
        ),
        ingestion_max_attempts=settings.ingestion_max_attempts,
    )
    knowledge_management = KnowledgeManagementService(
        SqlAlchemyKnowledgeUnitOfWork(
            database.sessions,
            SqlAlchemyEntitlementRepository,
        )
    )
    object_storage = MinioObjectStorage(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key.get_secret_value(),
        bucket=settings.minio_bucket,
    )
    secret_cipher = EnvelopeSecretCipher(MasterKeyFile(settings.master_key_path))
    provider_url_policy = StrictProviderBaseUrlPolicy(settings.model_provider_allowed_hosts)
    model_provider_configurations = ModelProviderConfigurationService(
        SqlAlchemyModelProviderUnitOfWork(database.sessions),
        secret_cipher,
        provider_url_policy,
        OpenAiCompatibleCapabilityProbe(
            provider_url_policy,
            timeout_seconds=settings.model_provider_probe_timeout_seconds,
        ),
    )
    ai_runtime_configurations = AiRuntimeConfigurationService(
        SqlAlchemyRuntimeConfigurationUnitOfWork(database.sessions)
    )
    runtime_bootstrap = (
        LocalMockRuntimeBootstrap(database.sessions, secret_cipher, ai_runtime_configurations)
        if settings.local_mock_bootstrap_enabled
        else None
    )
    assistant_conversations = AssistantConversationService(
        SqlAlchemyAssistantUnitOfWork(database.sessions),
        runtime_bootstrap=runtime_bootstrap,
    )
    workflows = WorkflowDefinitionService(SqlAlchemyWorkflowUnitOfWork(database.sessions))
    policy = RbacPolicyDecisionPoint(resource_registry, policy_reader, field_registry)
    retrieval_planning = BoundedRetrievalPlanningService(
        SqlAlchemyRetrievalPlanningUnitOfWork(database.sessions),
        policy,
        field_registry,
        DeterministicHashEmbeddingAdapter(),
        safety_gate=rag_safety,
    )
    retrieval_evidence = RetrievalEvidenceService(
        SqlAlchemyEvidenceProcessingUnitOfWork(database.sessions),
        policy,
        field_registry,
        DeterministicLexicalReranker(),
    )
    runtime_reader = SqlAlchemyRuntimeConfigurationReader(database.sessions)
    http_runtime_provider_factory = OpenAiCompatibleRuntimeProviderFactory(provider_url_policy)
    runtime_provider_factory = (
        LocalMockRuntimeProviderFactory(
            http_runtime_provider_factory,
            provider_id=LOCAL_MOCK_PROVIDER_ID,
            provider_key=LOCAL_MOCK_PROVIDER_KEY,
        )
        if settings.local_mock_bootstrap_enabled
        else http_runtime_provider_factory
    )
    model_runtime = RuntimeModelGatewayService(
        runtime_reader,
        SqlAlchemyRuntimeInvocationStore(database.sessions),
        model_provider_configurations,
        runtime_provider_factory,
        rag_safety,
    )
    streaming = TransactionalStreamService(
        SqlAlchemyStreamUnitOfWork(database.sessions),
        StreamPolicy(
            retention_seconds=settings.stream_retention_seconds,
            replay_limit_events=settings.stream_replay_limit_events,
            replay_limit_bytes=settings.stream_replay_limit_bytes,
        ),
    )
    assistant_run_executor = AssistantRunExecutor(
        assistant_conversations,
        retrieval_planning,
        retrieval_evidence,
        runtime_reader,
        model_runtime,
        AuthorizedModelContextBuilder(field_projection, rag_safety),
        streaming,
        delta_batch_characters=settings.stream_delta_batch_characters,
    )
    # 3. 容器接管全部资源；构造中途失败时按依赖逆序关闭，避免泄漏连接和缓存客户端。
    try:
        return ApplicationContainer(
            settings=settings,
            database=database,
            errors=ErrorCatalog.load(settings.error_catalog_path),
            resource_registry=resource_registry,
            field_policy_registry=field_registry,
            field_projection=field_projection,
            policy=policy,
            role_permissions=RolePermissionService(
                resource_registry,
                SqlAlchemyRolePermissionUnitOfWork(database.sessions),
                field_registry,
            ),
            menu_configuration=menu_configuration,
            menu_releases=menu_releases,
            knowledge_facts=knowledge_facts,
            knowledge_management=knowledge_management,
            knowledge_uploads=KnowledgeUploadService(
                knowledge_facts,
                object_storage,
                BoundedUploadInspector(settings.upload_max_file_size_bytes),
                DeterministicUploadScanner(),
            ),
            model_provider_configurations=model_provider_configurations,
            ai_runtime_configurations=ai_runtime_configurations,
            model_runtime=model_runtime,
            assistant_conversations=assistant_conversations,
            assistant_run_executor=assistant_run_executor,
            assistant_sources=AssistantSourceService(
                assistant_conversations,
                retrieval_evidence,
            ),
            streaming=streaming,
            retrieval_planning=retrieval_planning,
            retrieval_evidence=retrieval_evidence,
            workflows=workflows,
            rag_safety=rag_safety,
            authentication=AuthenticationService(
                repository=reader,
                sessions=sessions,
                passwords=passwords,
                secrets_digester=digester,
                session_ttl_seconds=settings.session_ttl_seconds,
                entitlements=entitlement_access,
            ),
            api_keys=ApiKeyService(
                repository=reader,
                unit_of_work=SqlAlchemyIdentityUnitOfWork(database.sessions),
                secrets_digester=digester,
                entitlements=entitlement_access,
            ),
            registration=RegistrationService(
                repository=reader,
                unit_of_work=SqlAlchemyRegistrationUnitOfWork(database.sessions),
                passwords=passwords,
            ),
            enterprise_workspaces=EnterpriseWorkspaceService(
                unit_of_work=SqlAlchemyEnterpriseUnitOfWork(database.sessions),
            ),
            entitlements=EntitlementService(
                unit_of_work=SqlAlchemyEntitlementUnitOfWork(database.sessions),
            ),
            organization=OrganizationService(
                unit_of_work=SqlAlchemyOrganizationUnitOfWork(database.sessions),
            ),
            roles=RoleService(
                unit_of_work=SqlAlchemyRoleUnitOfWork(database.sessions),
                cache=role_cache,
            ),
            role_cache=role_cache,
            secret_cipher=secret_cipher,
            sessions=sessions,
        )
    except Exception:
        role_cache.close()
        sessions.close()
        database.close()
        raise
