# 平台 API

本目录承载 Python 3.12、FastAPI 和 Pydantic 2 模块化单体。工作空间隔离、接口权限、字段级 ABAC、审计和模型网关都属于服务端安全边界。

本目录的新增和修改统一执行 [`docs/governance/backend-code-standards.md`](../../docs/governance/backend-code-standards.md)。

本地启动：

```bash
uv run uvicorn ai_platform_api.main:app --app-dir apps/api/src --host 127.0.0.1 --port 8000
```

健康端点为 `/api/v1/health/live` 和 `/api/v1/health/ready`，OpenAPI 文档位于 `/api/docs`。

## 应用装配与模块边界

`ai_platform_api.main` 只保留 ASGI 入口，正式装配集中在 `app/factory.py`：

- `app/` 创建应用、注册 Middleware、统一异常映射并管理进程级依赖生命周期。
- `modules/{domain}/api` 处理 HTTP 协议，`application` 编排用例，`domain` 保存业务规则，`infrastructure` 提供 Adapter。
- `ApplicationContainer` 持有进程级 Engine、Session Factory 和错误目录；业务模块只接收实际需要的窄接口，不读取容器本身。
- 应用启动时校验配置的 `ReleaseManifest` 与兼容矩阵；缺失、损坏或组合不兼容时拒绝启动。仓库默认文件是全合成 Golden Fixture，只验证机制，不代表真实发布制品。
- 已知平台异常按 `contracts/errors/catalog.v1.json` 映射；未知异常只返回 `INTERNAL_ERROR`，不会把数据库、供应商或 Python 原始异常暴露给客户端。

## 数据库 Migration 与集成测试

平台使用 Alembic 管理唯一一条 PostgreSQL Schema 演进流水线。生产和共享数据环境不随应用启动自动迁移；本地手动升级命令为：

```bash
uv run --locked alembic upgrade head
```

数据库、权限、Outbox 和幂等测试会创建随机临时 Schema，并在结束后清理。默认连接本地开发 PostgreSQL，也可使用 `AI_PLATFORM_TEST_DATABASE_URL` 指定独立测试数据库：

```bash
AI_PLATFORM_TEST_DATABASE_URL='postgresql+psycopg://user:password@127.0.0.1:5432/database' \
  uv run --locked pytest tests/integration
```

`P1A-03` 额外固定 `base → head → base → head` 往返门禁，并比较两次 Head 的列、约束和索引快照。Down Migration 只用于本地可逆性检查；真实数据升级仍依赖执行前备份和恢复演练，不能把 downgrade 当作唯一恢复手段。

## 混合检索与模型验证

P0-08 建立了权限约束的关键词检索、pgvector cosine 检索、RRF 合并、FastPass、可替换 Reranker、受控全文精读和引用校验基线。所有检索、精读和引用入口都必须接收由策略结果转换出的 `AuthorizedSearchScope`，并在 SQL 查询阶段应用工作空间、索引版本、知识库、文档、部门、可见性、密级和启用状态过滤。

默认 API/Worker 运行环境只安装 `pgvector` 客户端，不安装 Torch、Transformers 或模型文件。真实模型验收属于显式的本地技术验证，先安装独立依赖组并准备被 Git 忽略的本地缓存：

```bash
uv sync --frozen --group dev --group ai-validation
.venv/bin/python scripts/validate_bge_models.py \
  --cache-dir .ai-platform/models/huggingface
```

脚本固定验证 `BAAI/bge-m3` 的 1024 维归一化向量和 `BAAI/bge-reranker-v2-m3` 的中文证据排序。该脚本不联网、不进入 `./scripts/verify`，输出只用于小规模功能验证，不代表容量认证或线上质量结论。
