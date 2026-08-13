# 阶段 1 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 1：工作空间、企业治理与知识问答 MVP |
| 状态 | 进行中 |
| 报告日期 | 2026-08-13 |
| 当前节点 | `P1A-04` 进行中 |
| `core_functional` | `not_run` |
| `provider_integration` | `not_configured` |
| `ai_quality` | `not_configured` |
| `capacity_certification` | `not_run` |
| Linux 宿主机验收 | `not_run` |

## 2. 验证环境基线

| 项目 | 当前值 |
| --- | --- |
| 主要开发环境 | macOS 26.5.2，Apple Silicon `arm64`，18 GB 内存 |
| Node.js / pnpm | 24.19.0 / 11.20.0 |
| 项目 Python | 3.12.12，由 uv 管理 |
| 容器运行时 | Docker Desktop 4.86.0，Docker Engine 29.7.2，Compose v5.3.1 |
| 数据范围 | 仅使用合成个人与企业数据 |
| 模型范围 | 默认 Mock Provider；真实供应商由平台配置后单独验收 |

## 3. 节点验证记录

节点开始实施后按 [`阶段 1 实施计划`](./stage-1-plan.md) 持续追加以下事实：完成日期、测试命令、结果计数、容器或浏览器检查、已知限制和提交 SHA。未实际执行的检查保持 `not_run`，未配置的外部能力保持 `not_configured`。

### P1A-01 阶段契约与 ReleaseManifest

- 状态：通过。
- 核心实体：在不破坏 V1 旧消费者的前提下，为核心契约新增 `Account`、`Workspace`、`Conversation` 和 `Message` 可选定义；业务实体携带工作空间、版本和审计字段，消息内容继续复用已冻结的 `MessagePart`。
- 发布清单：新增 `ReleaseManifest V1` 和兼容矩阵 Schema，固定 Web、API、Worker、契约、数据库 Revision、Node/Python 运行时、镜像引用、平台和 `sha256` 摘要。构建时间、主机路径和随机标识不进入清单，自摘要覆盖除摘要字段外的全部内容。
- 深模块接口：`ReleaseManifestService.build` 对输入排序并生成稳定摘要；`validate` 一次聚合摘要、Schema、矩阵、组件 SemVer、数据库、运行时和镜像不兼容原因，启动器和升级器无需复制版本规则。
- 生成骨架：`scripts/generate_release_manifest.py` 接受构建系统提供的不可变元数据，生成前执行兼容检查，并支持 `--check` 漂移门禁。仓库 Golden Manifest 只使用全合成摘要，不代表真实镜像或可发布产物。
- 错误契约：新增 `RELEASE_MANIFEST_INVALID` 和 `RELEASE_COMBINATION_INCOMPATIBLE`，区分输入/摘要无效与运行组合冲突。
- 自动化验收：契约与发布模块专项测试 `18/18`，统一门禁 pytest `104/104`；覆盖 JSON Schema、Golden Fixture、可复现排序、自摘要防篡改、有效组合和一次返回多项不兼容原因。前端测试/构建、Ruff、mypy strict、架构检查、Manifest 漂移和相对 `HEAD` 的同主版本兼容检查通过。
- 已知警告：Starlette `TestClient` 提示后续迁移 `httpx2`，已列入 `P1A-06` 工程依赖事项，不影响当前契约与发布模块行为。
- 当前边界：真实发布镜像摘要由后续构建环境提供，当前不把合成 Fixture 描述为发布制品；启动装载与不兼容拒绝逻辑已在 `P1A-03` 接入，发布级制品注入和归档仍由 `P1A-06` 完成。
- 提交：`bbbd859`。

### P1A-02 Valkey 替换与兼容验收

- 状态：通过。
- 固定镜像：默认编排使用 Valkey `8.1.5-alpine@sha256:918228e4ff7da6b3a4213cb18067f6e09d9f0503d0a08868699ba227cff71861`；远程清单确认包含 `linux/amd64` 和 `linux/arm64`，本机 ARM64 容器报告 `server_name=valkey`、`valkey_version=8.1.5`。
- 配置迁移：Compose 服务、应用配置、API 健康键、平台诊断和环境示例统一改为 `valkey`；连接 URI 保留 RESP 客户端通用的 `redis://` Scheme。架构门禁同时禁止领域、应用和 API 层直接依赖 Redis 或 Valkey Client。
- 数据边界：Valkey 使用全新的 `.ai-platform/data/valkey` AOF 目录；阶段 0 的 `.ai-platform/data/redis` 原样保留但不挂载、不自动导入。旧 Redis 合成标记在新 Valkey 中不存在，新 Valkey 合成标记在容器重启后成功恢复。
- 编排迁移：`./platform start` 使用 `--remove-orphans` 移除改名前的旧 Redis 容器，但不删除宿主机数据目录。切换后容器清单只包含 Valkey，API、Worker 和 Web 按健康依赖顺序重建。
- 发布兼容：兼容矩阵升级为 `p1a-02-v1`，必需镜像从 `redis` 切换为 `valkey`，Golden Manifest 记录 Valkey 固定摘要与两个目标平台并重新生成自摘要。
- 自动化验收：配置、Compose、架构反例、API 健康、SSE 领域与真实 PostgreSQL 回归专项测试 `26/26`，统一门禁 pytest `108/108`；Compose 结构测试固定服务名、镜像摘要、命令、健康检查、新目录和 API/Worker 依赖。前端测试/构建、Ruff、mypy、架构、契约兼容、Manifest 漂移和 Compose 配置检查通过。
- 容器验收：一键构建与启动成功；完整 `./platform stop → start → doctor` 演练后 Web、API、MinIO、Tika、PostgreSQL、Valkey 和 Worker 七项诊断全部通过，Valkey 合成标记仍可恢复；API 就绪响应返回 `valkey=ok`。
- 当前边界：本节点验证 RESP、AOF、健康与当前应用/SSE 回归；Session、Celery 队列、分布式锁和 SSE 实时唤醒尚未进入业务实现，后续接入时必须继续执行兼容测试。旧 Redis 数据目录由管理员确认无需回滚后再人工归档，不在本节点删除。
- 提交：`faaa2a2`。

### P1A-03 模块化应用与 Migration 底座

- 状态：通过。
- 正式装配：`main.py` 缩减为 ASGI 入口；`app/factory.py` 集中创建 FastAPI、注册 Trace Middleware、异常处理、Router 和生命周期；`ApplicationContainer` 统一持有配置、错误目录、数据库 Engine 与 Session Factory，并在应用关闭时释放连接池。
- 模块边界：阶段 0 的全局 `routes/health.py` 和 `schemas.py` 已迁入真实的 `modules/system/api`；新增架构反例固定 API 不得依赖 SQLAlchemy、API/Application 不得直接导入 Infrastructure，现有依赖检查继续覆盖跨模块私有 Adapter。
- 错误契约：新增平台错误共同边界，现有 Workspace、Retrieval、Streaming 和 Model Gateway 可预期异常携带稳定错误码；HTTP 边缘从版本化错误目录统一生成状态码、公开文案、重试语义、Request ID 和 Trace ID。请求校验、404、405 和未知异常均不会泄漏框架、数据库或供应商内部信息。
- 启动兼容门：ReleaseManifest JSON 解析从脚本收敛到 Release 模块，应用装配与生成器复用相同的类型和兼容规则；清单缺失、字段或摘要损坏、Schema 版本篡改、数据库 Revision 或运行组合不兼容时拒绝启动。当前本地镜像携带全合成 Golden Manifest 验证机制，真实构建摘要仍由 `P1A-06` 注入。
- 数据库基线：Engine 固定 `pool_pre_ping`，Session Factory 固定 `autoflush=False`、`expire_on_commit=False`，每次调用产生独立 Session；事务失败由 Unit of Work 回滚，进程关闭统一释放 Engine。
- Migration 往返：真实 PostgreSQL 随机 Schema 从空库升级到 `20260813_0003`，完整降级到 `base` 后业务表清空，再次升级到 Head；两次 Head 的列、约束和索引快照完全一致。该检查不改变真实环境依赖备份与恢复演练的规则。
- 自动化验收：应用装配、错误映射、Session、契约、发布兼容和架构专项回归 `46/46`；统一 `./scripts/verify` 通过，前端格式/Lint/类型/测试/构建、Ruff、mypy strict、架构、契约兼容、SBOM、许可证和全量 pytest `123/123` 均通过。
- 容器验收：API 镜像重建成功，启动时成功装载并校验 Manifest；Web、API、MinIO、Tika、PostgreSQL、Valkey 和 Worker 七项 `./platform doctor` 诊断全部通过。
- 已知警告：Starlette `TestClient` 的 `httpx2` 迁移仍后置到 `P1A-06`，本节点没有新增静默跳过项。
- 当前边界：本节点只建立进程级依赖和错误/Migration 基线，不实现浏览器 Session、API Key、身份认证或工作空间成员校验；这些能力进入 `P1A-04`。
- 提交：本提交。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、质量、成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机兼容和生产容量结论。
- 当前没有真实企业客户，阶段 1 使用固定的合成企业空间完成产品与安全验收。
- 镜像扫描工具尚未完成认证或本地可用配置，阶段 1A 必须如实补齐或保持失败状态。

## 5. 阶段结论

`not_run`。阶段 0 已关闭，当前从 `P1A-01` 开始记录。
