# 平台 API

本目录承载 Python 3.12、FastAPI 和 Pydantic 2 模块化单体。工作空间隔离、接口权限、字段级 ABAC、审计和模型网关都属于服务端安全边界。

本地启动：

```bash
uv run uvicorn ai_platform_api.main:app --app-dir apps/api/src --host 127.0.0.1 --port 8000
```

健康端点为 `/api/v1/health/live` 和 `/api/v1/health/ready`，OpenAPI 文档位于 `/api/docs`。
