# 阶段 1 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 1：工作空间、企业治理与知识问答 MVP |
| 状态 | 进行中 |
| 报告日期 | 2026-08-14 |
| 当前节点 | `P1A-06` 待开始 |
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
- Migration 往返：本节点验收时真实 PostgreSQL 随机 Schema 从空库升级到当时的 Head `20260813_0003`，完整降级到 `base` 后业务表清空，再次升级到 Head；两次 Head 的列、约束和索引快照完全一致。后续 Revision 由各自节点继续执行相同回归，该检查不改变真实环境依赖备份与恢复演练的规则。
- 自动化验收：应用装配、错误映射、Session、契约、发布兼容和架构专项回归 `46/46`；统一 `./scripts/verify` 通过，前端格式/Lint/类型/测试/构建、Ruff、mypy strict、架构、契约兼容、SBOM、许可证和全量 pytest `123/123` 均通过。
- 容器验收：API 镜像重建成功，启动时成功装载并校验 Manifest；Web、API、MinIO、Tika、PostgreSQL、Valkey 和 Worker 七项 `./platform doctor` 诊断全部通过。
- 已知警告：Starlette `TestClient` 的 `httpx2` 迁移仍后置到 `P1A-06`，本节点没有新增静默跳过项。
- 当前边界：本节点只建立进程级依赖和错误/Migration 基线，不实现浏览器 Session、API Key、身份认证或工作空间成员校验；这些能力进入 `P1A-04`。
- 提交：`48b713a`。

### P1A-04 账号认证与可信上下文

- 状态：通过。
- 决策边界：新增 [`ADR-002`](../decisions/ADR-002-local-identity-and-secret-boundaries.md)，采用服务端 Session、逐请求 PostgreSQL 事实复核和分类型秘密存储；不把客户端账号、工作空间、Actor 或 Scope 声明直接作为授权事实。
- 账号与 Session：账号密码使用 Argon2id 独立盐哈希，错误账号和错误密码返回相同公开错误；浏览器 Session 使用高熵随机 Token，Valkey 键只保存 Token 摘要，载荷只包含账号 ID、认证版本和 CSRF 摘要，默认有效期 12 小时。
- 可信上下文：HTTP 边缘从已验证的浏览器 Session 或 Open API Key 重建 `RequestContext`，并逐请求复核账号、空间和成员状态；伪造空间、成员停用和账号认证版本变化均立即失败关闭。浏览器写请求额外验证与 Session 绑定的 CSRF Token。
- Open API Key：每个 Key 使用独立 Actor 和最小 Scope，明文只在签发时返回一次；PostgreSQL 只保存 SHA-256 摘要、末四位、有效期和撤销状态，撤销即时生效。Scope 后续在 `P1C` 与统一权限策略取交集，不能扩大创建者权限。
- 可逆秘密：为后续模型供应商 Key 建立 AES-256-GCM 信封加密 Adapter，每条秘密使用独立数据密钥和关联数据；本地启动器生成 32 字节 `0600` 主密钥，API 容器只读挂载，普通身份流程采用延迟读取。Open API Key 不使用可逆加密。
- 数据库与契约：新增账号、工作空间、成员和 Open API Key 表及 Migration `20260813_0004`；ReleaseManifest、兼容矩阵、错误目录和身份 OpenAPI 增量同步，并通过相对 `HEAD` 的同主版本兼容检查。
- 自动化验收：身份单元、HTTP 与契约专项 `24/24`；真实 PostgreSQL/Valkey 专项 `5/5`，覆盖随机 Schema Migration 往返、Session 摘要、跨空间拒绝、成员变更即时生效、Key 明文不落库、撤销和认证版本失效。统一 `./scripts/verify` 通过，前端格式/Lint/类型/测试/构建、Ruff、mypy strict、架构、契约、SBOM 和许可证检查均通过，全量 pytest `138/138`。
- 容器验收：API 与 Worker 镜像包含冻结的新依赖并成功重建；宿主机主密钥为 `0600` 的 32 字节普通文件，API 容器内为只读挂载；Web、API、MinIO、Tika、PostgreSQL、Valkey 和 Worker 七项诊断全部通过。
- 已知警告：Starlette `TestClient` 的 `httpx2` 迁移仍由 `P1A-06` 处理，本节点没有新增静默跳过项。
- 当前边界：本节点不实现注册、默认个人空间、企业成员生命周期、角色或 ABAC；这些分别进入 `P1B` 和 `P1C`。模型供应商凭证表和轮换演练分别进入 `P1D-05` 与 `P1G-03`。
- 提交：`2e4cf92`。

### P1A-05 审计、Outbox 与 Trace 运行链

- 状态：通过。
- 共享内核：新增 `packages/backend`，只承载 API 与 Worker 已发生真实复用的数据库、版本化集成事件、不可变审计、Outbox、幂等消费、任务信封和 W3C Trace 能力；共享内核不依赖 FastAPI 或 Celery 入口，并已纳入分层依赖门禁。
- 事务与审计：工作空间写用例在同一 PostgreSQL 事务提交业务事实、审计事实和 Outbox；审计属性不复制敏感正文，数据库 Trigger 拒绝更新和删除，修正只能追加新记录。任一约束失败时三类事实整体回滚。
- Outbox 调度：使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 和短租约认领到期事件；发布失败执行有限指数退避，达到最大尝试次数或最后一次租约过期进入死信。发布后丢失确认允许重复投递，租约归属变化不会被误计为已重试。
- Celery 与事实边界：新增 Celery 5.5.3，Valkey 只负责 Broker 和唤醒，PostgreSQL 保存 Outbox、尝试、死信、消费回执和投影事实。Beat 周期调度 `platform.outbox.dispatch.v1`，Worker 消费 `platform.integration.consume.v1`；任务只使用 JSON，不启用结果后端，固定 Late Ack、Worker 丢失拒绝和 Prefetch 1。
- 可信任务与 Trace：内部任务采用独立 32 字节 HMAC 密钥签名规范 JSON；Worker 验签且核对任务名后才恢复 Actor、User、Workspace、Request 和 Trace。消费时保持原 `trace_id` 并生成新 Span，消费回执与投影在同一事务提交，重复事件不重复产生副作用。
- Migration 与本地编排：新增 Revision `20260813_0005`；本地 Compose 增加一次性 `migrate` 服务，成功后才启动 API 与 Worker。Alembic 显式读取容器数据库 URL，`./platform doctor` 新增 Revision 与真实 Celery Ping 检查。API 只读挂载主密钥，Worker 只读挂载独立任务签名密钥，二者不互相暴露。
- 契约与供应链：集成事件兼容新增可信主体字段，新增内部任务信封 Schema/Fixture，ReleaseManifest 推进到 `20260813_0005`。SBOM 和许可证产物加入 Celery 及依赖；Node 产物改从冻结安装图和包清单生成，不依赖机器本地 pnpm Store 索引。
- 自动化验收：专项单元、契约、结构和工程门禁 `39/39`；真实 PostgreSQL/Valkey 回归 `17/17`，覆盖 Migration 往返、审计不可变、事务回滚、租约恢复、有限重试、死信、重复消费、身份与隔离。统一 `./scripts/verify` 通过，前端格式/Lint/类型/测试/构建、Ruff、mypy strict、架构、契约、SBOM 和许可证检查均通过，全量 pytest `155/155`。
- 容器验收：API、Worker 与 Migration 镜像从冻结锁文件重建；Migration 从公共空 Schema 升级至 `20260813_0005` 后以状态 0 退出；Celery 实际注册两个版本化任务。固定合成事件最终为 `published|1|1|1`，即一次发布尝试、一个消费回执和一次投影，投影 Trace 与原事件保持同一 Trace。宿主机两类密钥均为 32 字节 `0600`，容器内按职责只读挂载；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision、Valkey 和 Worker 八项诊断全部通过。
- 已知警告：Starlette `TestClient` 的 `httpx2` 迁移仍由 `P1A-06` 处理；本节点没有把 Broker 当作最终事实，也没有引入 Durable Run 或后置能力。
- 当前边界：本节点只建立通用可靠运行链和示例投影，不实现业务任务管理页面、人工死信恢复、业务告警或分布式 Trace 后端；发布级扫描、Secret Scanner 和制品归档进入 `P1A-06`。
- 提交：本提交。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、质量、成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机兼容和生产容量结论。
- 当前没有真实企业客户，阶段 1 使用固定的合成企业空间完成产品与安全验收。
- 镜像扫描工具尚未完成认证或本地可用配置，阶段 1A 必须如实补齐或保持失败状态。

## 5. 阶段结论

`not_run`。阶段 0 已关闭，当前从 `P1A-01` 开始记录。
