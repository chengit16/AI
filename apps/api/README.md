# 平台 API

本目录承载 Python 3.12、FastAPI 和 Pydantic 2 模块化单体。工作空间隔离、接口权限、字段级 ABAC、审计和模型网关都属于服务端安全边界。

本目录的新增和修改统一执行 [`docs/governance/backend-code-standards.md`](../../docs/governance/backend-code-standards.md)。

本地启动：

```bash
uv run uvicorn ai_platform_api.main:app --app-dir apps/api/src --host 127.0.0.1 --port 8000
```

健康端点为 `/api/v1/health/live` 和 `/api/v1/health/ready`，OpenAPI 文档位于 `/api/docs`。

## 数据库 Migration 与集成测试

阶段 0 使用 Alembic 管理 PostgreSQL Schema。生产和共享数据环境不随应用启动自动迁移；本地手动升级命令为：

```bash
uv run --locked alembic upgrade head
```

数据库、权限、Outbox 和幂等测试会创建随机临时 Schema，并在结束后清理。默认连接本地开发 PostgreSQL，也可使用 `AI_PLATFORM_TEST_DATABASE_URL` 指定独立测试数据库：

```bash
AI_PLATFORM_TEST_DATABASE_URL='postgresql+psycopg://user:password@127.0.0.1:5432/database' \
  uv run --locked pytest tests/integration
```
