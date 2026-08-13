from dataclasses import dataclass

from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.config import Settings
from ai_platform_api.modules.release.application.startup import verify_release_compatibility
from ai_platform_api.persistence.database import PlatformDatabase


@dataclass(frozen=True)
class ApplicationContainer:
    """集中持有进程级依赖，业务模块只接收其实际需要的窄接口。"""

    settings: Settings
    database: PlatformDatabase
    errors: ErrorCatalog

    def close(self) -> None:
        self.database.close()


def build_application_container(settings: Settings) -> ApplicationContainer:
    verify_release_compatibility(
        settings.release_manifest_path,
        settings.compatibility_matrix_path,
    )
    return ApplicationContainer(
        settings=settings,
        database=PlatformDatabase.create(settings.database_url),
        errors=ErrorCatalog.load(settings.error_catalog_path),
    )
