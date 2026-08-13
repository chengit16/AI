from fastapi import FastAPI

from ai_platform_api.config import get_settings
from ai_platform_api.routes.health import router as health_router


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="AI 智能平台 API",
        summary="个人空间与企业空间共用的平台服务接口",
        version=settings.version,
        openapi_version="3.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    application.include_router(health_router, prefix="/api/v1")
    return application


app = create_app()
