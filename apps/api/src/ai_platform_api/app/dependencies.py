from dataclasses import dataclass

from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.config import Settings
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
from ai_platform_api.modules.release.application.startup import verify_release_compatibility
from ai_platform_api.persistence.database import PlatformDatabase


@dataclass(frozen=True)
class ApplicationContainer:
    """集中持有进程级依赖，业务模块只接收其实际需要的窄接口。"""

    settings: Settings
    database: PlatformDatabase
    errors: ErrorCatalog
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
    try:
        return ApplicationContainer(
            settings=settings,
            database=database,
            errors=ErrorCatalog.load(settings.error_catalog_path),
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
