"""API 进程入口，使用统一工厂创建可部署的 FastAPI 应用。"""

from ai_platform_api.app.factory import create_app

app = create_app()
