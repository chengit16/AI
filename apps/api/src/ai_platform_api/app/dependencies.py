from dataclasses import dataclass

from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.config import Settings
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
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
    secret_cipher: EnvelopeSecretCipher
    sessions: ValkeySessionStore

    def close(self) -> None:
        try:
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
    reader = SqlAlchemyIdentityReader(database.sessions)
    digester = Sha256SecretDigester()
    try:
        return ApplicationContainer(
            settings=settings,
            database=database,
            errors=ErrorCatalog.load(settings.error_catalog_path),
            authentication=AuthenticationService(
                repository=reader,
                sessions=sessions,
                passwords=Argon2idPasswordAdapter(),
                secrets_digester=digester,
                session_ttl_seconds=settings.session_ttl_seconds,
            ),
            api_keys=ApiKeyService(
                repository=reader,
                unit_of_work=SqlAlchemyIdentityUnitOfWork(database.sessions),
                secrets_digester=digester,
            ),
            secret_cipher=EnvelopeSecretCipher(MasterKeyFile(settings.master_key_path)),
            sessions=sessions,
        )
    except Exception:
        sessions.close()
        database.close()
        raise
