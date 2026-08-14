from dataclasses import dataclass, field
from pathlib import Path

from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.config import Settings
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
from ai_platform_api.modules.knowledge.application.uploads import KnowledgeUploadService
from ai_platform_api.modules.knowledge.infrastructure.object_storage import MinioObjectStorage
from ai_platform_api.modules.knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyKnowledgeUnitOfWork,
)
from ai_platform_api.modules.knowledge.infrastructure.upload_security import (
    BoundedUploadInspector,
    DeterministicUploadScanner,
)
from ai_platform_api.modules.release.application.startup import verify_release_compatibility
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
    resource_registry = load_resource_registry(Path(settings.resource_registry_path))
    field_registry = load_field_policy_registry(Path(settings.field_policy_registry_path))
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
    object_storage = MinioObjectStorage(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key.get_secret_value(),
        bucket=settings.minio_bucket,
    )
    try:
        return ApplicationContainer(
            settings=settings,
            database=database,
            errors=ErrorCatalog.load(settings.error_catalog_path),
            resource_registry=resource_registry,
            field_policy_registry=field_registry,
            field_projection=FieldProjectionService(field_registry),
            policy=RbacPolicyDecisionPoint(resource_registry, policy_reader, field_registry),
            role_permissions=RolePermissionService(
                resource_registry,
                SqlAlchemyRolePermissionUnitOfWork(database.sessions),
                field_registry,
            ),
            menu_configuration=menu_configuration,
            menu_releases=menu_releases,
            knowledge_facts=knowledge_facts,
            knowledge_uploads=KnowledgeUploadService(
                knowledge_facts,
                object_storage,
                BoundedUploadInspector(settings.upload_max_file_size_bytes),
                DeterministicUploadScanner(),
            ),
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
            secret_cipher=EnvelopeSecretCipher(MasterKeyFile(settings.master_key_path)),
            sessions=sessions,
        )
    except Exception:
        role_cache.close()
        sessions.close()
        database.close()
        raise
