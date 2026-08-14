# 阶段 1 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 1：工作空间、企业治理与知识问答 MVP |
| 状态 | 进行中 |
| 报告日期 | 2026-08-14 |
| 当前节点 | `P1D-07` 待开始 |
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
- 提交：`b14c4b6`。

### P1A-06 供应链与本地门禁

- 状态：通过。
- 跨语言契约：在仓库根工具链隔离固定 `openapi-typescript 7.13.0` 和其支持的 TypeScript `5.9.3`，React 应用继续使用 TypeScript `6.0.2`；从同一 OpenAPI 可复现生成 React `readonly` 类型与 Python `TypedDict`，仓库生成器和 `./scripts/verify` 检查生成漂移，前后端不得维护分叉类型。
- Secret Scanner：覆盖跟踪、待跟踪文件和所有可达 Git Blob，检测敏感文件、私钥与常见平台令牌；历史命中只报告 Blob 短 ID 和路径，不输出凭证正文。合成临时 Git 仓库证明删除后的凭证仍会被发现。
- 状态门禁：`local-readiness.v1.json` 把本地开发与正式发布分开。契约生成、当前文件/历史 Secret Scanner、SBOM、许可证和 `ReleaseManifest` 为本地必需项并通过；镜像扫描为 `not_configured`，Linux 验收为 `not_run`，因此 `development_status=passed`、`release_status=blocked`。正式发布检查返回非零，未配置项没有静默通过。
- 发布归档：新增受门禁保护的归档命令；只有 `release_status=passed` 才能归档真实 `ReleaseManifest`、门禁状态、Python/Node SBOM、许可证与跨语言契约类型，并生成稳定 `SHA256SUMS`。真实扫描证据位于忽略的 `artifacts/`，不把本地状态 Fixture 冒充发布制品。
- 测试客户端：固定 `httpx2 2.10.0` 及锁定依赖，Starlette `TestClient` 不再回退到弃用的 `httpx` 兼容层；身份、契约和 Worker 相关测试 `45/45` 通过且没有原弃用警告。
- 自动化验收：工程与供应链专项 `20/20`；统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试/生产构建、Ruff、mypy strict、架构、契约兼容、契约生成、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest 均通过。正式发布门禁按预期以状态 1 拒绝当前未配置组合。
- 容器验收：现有冻结镜像组合未改变运行时功能；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision、Valkey 和 Worker 八项诊断全部通过。
- 当前边界：Docker Scout 1.24.0 已安装，但未获镜像组件和漏洞元数据外发授权，未执行扫描；正式流水线需授权 Scout 或使用完全本地的 Trivy/Grype。Linux 验收等待可用宿主机。两者不阻断 `P1B` 本地功能实施，也不得描述为正式发布通过。
- 提交：`f2d2a83`。

### P1B-01 账号与个人空间

- 状态：通过。
- 注册事务：新增公开注册用例，在一个 PostgreSQL 事务内创建账号、唯一默认个人空间、所有者成员关系、不可变审计和 Outbox 事件；任一写入失败时整体回滚。审计属性只记录空间类型，事件载荷只记录个人空间 ID，不复制登录名、显示名或密码。
- 账号与空间约束：登录名统一执行 `strip().casefold()`，数据库约束要求小写、去除首尾空格且至少 3 个字符；应用预检查处理常见重复，数据库唯一约束关闭并发竞态，对外稳定映射 `REGISTRATION_CONFLICT` 与 HTTP `409`。个人空间必须有所有者、企业空间不得设置个人所有者，每个账号最多拥有一个个人空间。
- 可信访问：个人空间除账号、空间和成员状态均有效外，还必须由当前账号实际拥有；非所有者即使存在活跃成员关系也失败关闭。注册接口只返回账号 ID 与默认个人空间 ID，不隐式建立登录会话，后续仍通过既有密码登录和服务端 Session 边界认证。
- Migration 与契约：新增 Revision `20260814_0006`，升级前显式拒绝不满足新约束的历史事实，不静默改写身份数据；完整 `base → head → base → head` 往返结构一致。OpenAPI、React/Python 生成类型、错误目录、ReleaseManifest、兼容矩阵和本地 Revision 诊断同步推进。
- 自动化验收：注册单元、HTTP、契约和 Worker 结构专项 `25/25`，真实 PostgreSQL 注册与 Migration 专项 `4/4`；统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试/生产构建、Ruff、mypy strict、架构、契约兼容与漂移、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest `167/167` 均通过。
- 容器验收：一键构建和 Migration 成功；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision `20260814_0006`、Valkey 和 Worker 八项诊断全部通过。使用全新合成账号执行真实 HTTP 闭环，注册 `201`、登录 `200`、个人空间上下文 `200`、重复注册 `409`，账号与空间事实一致。
- 当前边界：本节点不实现企业空间创建、邀请、加入、离开、停用或空间切换，这些能力进入 `P1B-02`；不实现组织、角色、菜单、ABAC 或审批。
- 提交：`eaf5011`。

### P1B-02 企业空间与成员生命周期

- 状态：通过。
- 企业所有者：浏览器 Session 用户可从任一当前可访问空间创建企业空间；企业创建者在同一事务内成为唯一、活跃且不可移除的 `owner`。数据库部分唯一索引保证每个空间最多一个所有者，领域状态机禁止所有者离开或被停用，避免角色系统实施前出现无人治理空间。
- 定向邀请：所有者按已注册且活跃的规范化登录名定向邀请成员，不依赖 SMTP；邀请默认 7 天有效，状态限定为 `pending / accepted / cancelled / expired`，同一账号在同一空间最多一个待处理邀请。过期邀请在再次邀请前关闭，邀请只能由目标账号接受且只能使用一次。
- 成员生命周期：成员状态限定为 `active / disabled / left`；首次接受邀请创建成员关系，已停用或已离开的成员通过新邀请恢复。成员主动离开、所有者停用成员以及重新加入均在行锁事务中更新成员聚合版本，相关 Outbox 事件版本固定按 `1 → 2 → 3 → 4` 递增。
- 可信治理边界：企业创建、邀请、接受、切换、离开、停用和成员列表均复用可信 `RequestContext`。空间切换不保存客户端“当前空间”事实，每次目标切换及后续请求重新查询 PostgreSQL；成员停用后下一次请求立即失败关闭。只有浏览器 Session 可以治理企业空间，即使 Open API Key 携带治理 Scope 也返回 `POLICY_DENIED`。
- 事务与数据约束：企业空间、所有者成员、邀请、成员状态、不可变审计和 Outbox 在同一 PostgreSQL 事务提交。数据库同时约束成员唯一性、所有者唯一性、成员/邀请状态、邀请接受时间与到期时间；旧结构存在无法验证所有者的企业成员时 Migration 拒绝猜测或静默提权。
- 契约与 Migration：新增企业空间和成员管理 OpenAPI，React/Python 生成类型、错误目录、ReleaseManifest 与兼容矩阵同步推进到 Revision `20260814_0007`。随机隔离 Schema 的 `base → head → base → head` 列、约束和索引快照一致；本地公共合成开发库因节点开发期间先运行旧 `0007`，已使用扩展式 `ADD COLUMN / ADD CONSTRAINT` 无损补齐成员 `version`，没有删除或改写成员状态。
- 自动化验收：企业领域、HTTP、契约和应用装配专项 `35/35`，企业及身份 PostgreSQL/Valkey 与 Migration 专项 `11/11`，成员事件版本专项 `3/3`。统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试/生产构建、Ruff、mypy strict、架构、契约兼容与漂移、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest `175/175` 均通过。
- 容器与 HTTP 验收：API、Worker、Web 和 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision `20260814_0007`、Valkey 和 Worker 八项诊断通过。使用全新合成双账号完成真实 HTTP 闭环：注册 `201/201`、登录 `200/200`、创建企业 `201`、邀请 `201`、接受 `200`、切换 `200`、停用前访问 `200`、停用 `200`，停用后立即返回 `403 POLICY_DENIED`。
- 当前边界：本节点不实现部门树、岗位、角色、菜单、接口权限、字段级 ABAC、套餐或审批；这些能力继续按 `P1B-03` 及后续节点实施。邀请当前只面向已注册账号，不扩展邮件投递、公开邀请码或 SaaS 租户注册。
- 提交：`bbb5398`。

### P1B-03 多级组织与岗位

- 状态：通过。
- 部门树：使用工作空间内的邻接关系作为写入事实，并在同一事务重建闭包表；领域计算拒绝环、悬空父节点和自引用，数据库复合外键拒绝跨空间父子关系。同级部门名称大小写不敏感且唯一，层级移动执行行锁、版本校验和完整闭包重建。
- 启停与继承范围：部门和岗位支持显式启用、停用与聚合版本；部门自身虽为启用状态，只要任一祖先停用，其 `effective_active` 即为 `false`。后续部门角色继承只使用自身及有效启用的后代范围，不会穿过已停用祖先。
- 岗位与成员归属：岗位必须属于有效部门，部门内名称大小写不敏感且唯一。活跃企业成员可同时属于最多 100 个部门并指定唯一主部门，岗位必须属于已分配且有效的部门；成员归属更新同步推进成员聚合版本。成员离开或停用时在同一事务清空部门与岗位归属，重新加入不隐式恢复旧权限。
- 可信治理边界：当前组织治理仅允许企业空间所有者通过浏览器 Session 执行；个人空间、普通成员、伪造当前空间和 Open API Key 均失败关闭。部门、岗位和成员归属写操作继续同事务提交不可变审计与 Outbox，事件按各自聚合版本递增。
- 数据与契约：新增部门、部门闭包、岗位、成员部门和成员岗位表，以及 Revision `20260814_0008`；工作空间复合外键和成员部门/岗位复合外键在数据库层关闭跨空间与岗位错配。组织 OpenAPI、React/Python 生成类型、错误目录、ReleaseManifest、兼容矩阵和本地 Revision 诊断同步推进。
- 自动化验收：组织领域与 HTTP 专项 `4/4`，真实 PostgreSQL 专项 `3/3`；统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试/生产构建、Ruff、mypy strict、架构、契约兼容与漂移、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest `183/183` 均通过。随机隔离 Schema 的 `base → head → base → head` 列、约束和索引快照一致。
- 容器与 HTTP 验收：API、Worker、Web 和 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision `20260814_0008`、Valkey 和 Worker 八项诊断通过。使用全新合成双账号完成真实 HTTP 闭环：注册 `201/201`、登录 `200/200`、企业与邀请 `201/201`、接受 `200`、根/子部门 `201/201`、岗位 `201`、成员归属写入/回读 `200/200`，普通成员访问组织治理稳定返回 `403 POLICY_DENIED`。
- 当前边界：本节点不实现空间角色、部门角色和确定性继承计算，也不实现菜单、接口权限、字段级 ABAC、套餐或审批；这些继续按 `P1B-04`、`P1C` 及后续节点实施。当前部门树采用全量闭包重建以优先保证本地 MVP 一致性，超大组织的增量闭包优化需由后续容量证据驱动。
- 提交：`a8bf242`。

### P1B-04 确定性角色继承

- 状态：通过。
- 角色事实：每个新旧工作空间均具有确定性 ID 的系统角色 `workspace_owner` 和 `workspace_member`。自定义角色在空间内按 `role_key` 和大小写不敏感名称保持唯一，支持启用与停用；系统角色不可停用，系统绑定不可由普通角色接口撤销。
- 绑定与继承：自定义角色支持 `workspace`、`department` 和 `member` 三类累加绑定。同一角色可从多个来源生效并按稳定顺序去重；部门绑定沿有效部门后代继承，成员只归属子部门时可获得祖先部门角色，任一祖先停用后不会继续传递。
- 治理与读取：角色写入和角色清单只允许企业所有者通过浏览器 Session 执行；普通成员只能读取自己的有效角色，所有者可读取企业成员的有效角色。个人空间、伪造当前空间、普通成员治理和跨账号读取均失败关闭。本节点只建立角色身份，不提前实现 `P1C` 的权限项、菜单、RBAC 决策或字段级 ABAC。
- 撤权与缓存：PostgreSQL 的 `workspaces.role_version` 是缓存失效事实，Valkey 键包含工作空间、成员和角色版本，只保存可重建结果。角色和绑定变化、部门移动或启停、成员组织归属和成员状态变化均推进版本；旧缓存无需同步删除也不能再次命中。成员停用或离开时清空组织归属并撤销自定义成员绑定，重新加入不隐式恢复旧角色。
- 数据与契约：新增 `roles`、`role_bindings` 和 `role_version`，Revision 推进到 `20260814_0009`。角色、部门和成员目标使用工作空间复合外键关闭跨空间绑定；Migration 为既有空间补齐系统角色与绑定并保持应用写入相同的确定性 ID。角色 OpenAPI、`ROLE_CONFLICT`、React/Python 生成类型、ReleaseManifest、兼容矩阵和本地 Revision 诊断同步推进。
- 自动化验收：角色领域和 HTTP 专项 `4/4`，真实 PostgreSQL/Valkey 专项 `3/3`，既有身份、企业、组织与 Migration 集成回归 `14/14`。统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试/生产构建、Ruff、mypy strict、架构、契约兼容与漂移、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest `191/191` 均通过；随机隔离 Schema 的 `base → head → base → head` 结构一致。
- 容器与 HTTP 验收：API、Worker、Web 和 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision `20260814_0009`、Valkey 和 Worker 八项诊断通过。使用全新合成双账号完成真实 HTTP 闭环：注册 `201/201`、登录 `200/200`、企业与邀请 `201/201`、接受 `200`、根/子部门 `201/201`、成员归属 `200`、角色/部门绑定 `201/201`、所有者和成员有效角色读取 `200/200`、普通成员治理 `403 POLICY_DENIED`、撤销和重新计算 `200/200`；角色版本由 `4` 推进至 `5`，自定义角色即时消失。
- 当前边界：本节点不定义权限码、菜单页面权限、接口绑定、字段级 ABAC、套餐配额或审批；这些继续按 `P1B-05`、`P1C` 及后续节点实施。Valkey 只承担性能优化，任何缓存损坏或丢失都可从 PostgreSQL 重建。
- 提交：`9ab81e2`。

### P1B-05 套餐配额与空间状态

- 状态：通过。
- 版本化权益：个人和企业共用 `WorkspaceEntitlement`、功能设置、用量计数器与幂等用量记录。默认本地个人套餐为 5 GB、1 成员、5 知识库、3 个已发布 Agent、月问答 2000 且不允许 Open API；模拟企业套餐为 100 GB、100 成员、50 知识库、20 个已发布 Agent、月问答 20000 且允许所有者配置 Open API。上述额度只用于功能验收，不代表商业价格或本地物理容量承诺。
- 原子配额：知识库、存储、已发布 Agent 使用 lifetime 计数，问答按 UTC 月份隔离。可信业务模块通过单一 `consume` 用例在工作空间行锁事务中完成上限检查、计数更新、幂等记录、审计与 Outbox；相同幂等键和参数返回原结果，参数冲突稳定拒绝，超额、负结果、停用空间均不产生部分写入。
- 成员与 Open API：接受邀请前在已锁定空间事务内检查活跃成员数，达到套餐上限返回 `QUOTA_EXCEEDED`。个人空间启用 Open API 返回 `ENTITLEMENT_DENIED`；企业普通成员可读取套餐但不能治理，所有者变更开关推进 `entitlement_version`。API Key 签发和每次认证都重新读取权益，关闭后已签发 Key 立即失效，浏览器 Session 不受该开关影响。
- 数据与契约：新增 `workspace_entitlements`、`workspace_feature_settings`、`workspace_usage_counters` 和 `workspace_usage_records`，Revision 推进到 `20260814_0010`；Migration 为既有个人与企业空间补齐默认权益。套餐 OpenAPI、三个稳定错误码、React/Python 生成类型、ReleaseManifest、兼容矩阵和本地 Revision 诊断同步推进；生成器现在对 Python 与 TypeScript 产物分别执行固定 Ruff/Prettier 配置，重复生成无漂移。
- 本地资源门禁：`AI_PLATFORM_MIN_FREE_DISK_GB` 可在项目启动环境中配置，默认仍为 50 GB。本次当前机器约 48 GB 可用磁盘，仅以进程级 40 GB 覆盖执行小规模功能验收；不把该结果描述为容量认证，也不降低仓库默认安全基线。
- 自动化验收：真实 PostgreSQL/Valkey、Migration 与身份回归专项 `8/8`，权益 Application 拆分后的专项 `5/5`。统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试/生产构建、Ruff、mypy strict、架构、契约兼容与漂移、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest `197/197` 均通过；随机隔离 Schema 的 `base → head → base → head` 结构一致。
- 容器与 HTTP 验收：API、Worker、Web 与 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision `20260814_0010`、Valkey 和 Worker 八项诊断通过。使用全新合成双账号完成真实 HTTP 闭环：注册 `201/201`、登录 `200/200`、个人套餐读取 `200`、个人启用 Open API `403 ENTITLEMENT_DENIED`、企业创建/邀请/接受 `201/201/200`、普通成员读取 `200` 且治理 `403 POLICY_DENIED`、所有者读取和启用 `200/200`，权益版本由 `1` 推进至 `2`。
- 当前边界：本节点不实现商业定价、购买订阅、SaaS 计费、普通 HTTP 用量写入、知识库/Agent/问答业务模块或页面；后续模块必须复用当前原子配额入口。套餐管理页面进入 `P1B-06`，菜单权限、字段级 ABAC 与审批继续按 `P1C` 及后续节点实施。
- 提交：`e18f015`。

### P1B-06 个人与企业空间管理页面

- 状态：通过。
- 正式前端结构：React 应用迁入 `app`、`routes`、`pages`、`components`、`hooks`、`api/services`、`store` 和 `styles` 分层；业务页面按路由懒加载，TanStack Query 管理服务端状态，Zustand 只保存当前账号、工作空间与 CSRF 会话状态。浏览器 Session 仅进入 `sessionStorage`，Cookie、CSRF、工作空间头与稳定错误码统一由 API Client 处理。
- 登录与空间闭环：登录响应在不破坏 V1 旧消费者的前提下新增可选 `personal_workspace_id`，服务端实际登录必须解析唯一有效个人空间后才创建 Session，前端拒绝缺失该字段的残缺响应。页面支持注册、登录、退出、个人/企业空间切换、创建企业空间和按邀请 ID 加入企业。
- 治理页面：空间总览展示类型、身份、状态、套餐权益、五类配额与 Open API 开关；成员页支持邀请、清单和停用；组织页支持多级部门、岗位和成员归属。个人空间显示对应空状态，企业普通成员访问成员和组织治理时由后端拒绝并显示无权限状态，前端菜单隐藏不作为安全边界。
- 交互与响应式：桌面使用可折叠固定侧栏，移动和低高度横屏使用顶部栏与抽屉导航；公共控件最小交互高度为 44 px。`375×812`、`390×844`、`768×1024`、`1024×768`、`1440×900` 和 `844×390` 六类视口无横向溢出；移动抽屉、成员页、组织页和普通成员无权限页均无控制台错误。
- 并发缺陷修复：真实组织页面的三个并发请求暴露应用容器复用 UoW 实例时共享 Session 的竞态。Identity、Registration、Enterprise、Entitlement、Organization 和 Role UoW 统一改为 `ContextVar` 隔离当前 Session 与 Repository/Writer，并以双线程重叠事务测试确认六类 UoW 不串线、不互相关闭 Session。
- 自动化验收：UoW 并发专项 `6/6`，Identity、Organization 与并发回归组合 `17/17`。统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试 `2/2`/生产构建、Ruff、mypy strict、架构、契约兼容与漂移、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest `204/204` 均通过。Ant Design 公共块约 `305.59 KB gzip`，业务路由块约 `1～9 KB gzip`；公共块警告记录为后续性能观察项，不在本地 MVP 阶段更换既定组件技术栈。
- 容器与浏览器验收：API、Worker、Web 与 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision `20260814_0010`、Valkey 和 Worker 八项诊断通过。使用全合成账号完成注册登录、个人空间读取、创建并自动切换企业空间、套餐读取、Open API 开关、部门和岗位创建、成员邀请与接受；普通企业成员访问成员及组织治理均稳定显示无权限状态。
- 当前边界：本节点使用静态注册菜单承载所有现有页面，不提前实现菜单草稿、动态发布、页面与接口统一权限码或字段级 ABAC；这些继续按 `P1C` 实施。页面不新增 SaaS、Go 运行层、真实多源连接器、LLM Grading、多模态图片问答、Channel Gateway 或 Durable Run。
- 提交：`6a7f2f6`。

### P1C-01 统一权限资源模型

- 状态：通过。
- 领域语言：新增根级 `CONTEXT.md`，固定 Permission、`permission_code`、`PageResource`、`ApiResource`、Menu、`ResourceRegistry` 和 `MenuRelease` 的边界。菜单只负责导航体验，Permission 才是后端授权语义；页面可见 Permission 与页面内创建、读取、停用等业务接口 Permission 相互独立，不能因能进入页面而自动获得写权限。
- 版本化注册表：新增 `ResourceRegistry V1` JSON Schema 和冻结注册表，显式登记 28 项 Permission、5 个页面资源、31 个 API 资源和 5 个系统菜单。登录、注册、健康等公共或仅认证入口也具有明确 `access_level`，不能通过不登记来绕过审计；当前所有 OpenAPI 操作均按 HTTP Method、路径和 `operation_id` 一一覆盖。
- 发布前校验：纯领域校验器一次聚合 Schema/Registry 版本、重复 ID/Key/路由/操作、非法 `permission_code`、非法组件/布局/路由、公共资源错误绑定权限、授权资源缺少或引用停用权限、悬空菜单父节点/页面/权限、菜单循环、启用菜单引用停用页面以及未绑定授权页面/Permission。生成器同步核对 OpenAPI 全覆盖并生成 React 只读消费产物，任何漂移进入 `./scripts/verify` 后失败。
- 前后端消费：API 进程启动时从 `Settings.resource_registry_path` 装载并验证同一注册表，API 镜像显式包含版本化契约；React 的路由路径、菜单名称、排序、图标键和页面 Permission 改为消费生成产物。页面组件仍通过本地受控 `component_key` 映射懒加载，注册表不能执行任意组件或任意路由。
- 自动化验收：注册表领域与反例 `4/4`，注册表加契约专项 `24/24`，API 启动与应用组合 `51/51`；统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试 `3/3`/生产构建、Ruff、mypy strict、架构、OpenAPI 覆盖、契约兼容与漂移、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest `209/209` 均通过。
- 容器与浏览器验收：API、Worker、Web 与 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision `20260814_0010`、Valkey 和 Worker 八项诊断通过。真实浏览器确认注册表生成的空间总览、成员管理、组织架构和运行状态四个菜单全部可见并可导航，普通成员无权限状态保持，页面无横向溢出且控制台无错误。
- 当前边界：本节点建立平台注册和发布前结构门禁，不把冻结注册表冒充工作空间 `MenuRelease`，不实现角色到 Permission 的实际授权、数据范围计算、页面/接口动态绑定、菜单草稿发布或字段级 ABAC；这些分别进入 `P1C-02` 至 `P1C-05`。注册表当前随代码发布，尚不允许管理员录入任意页面、组件、路由或接口。
- 提交：`087d8ac`。

### P1C-02 RBAC 与资源/数据级 ABAC

- 状态：通过。
- 授权事实：新增持久化 `RolePermissionGrant`，明确角色身份与业务 Permission 分离，并支持工作空间、部门子树、当前账号和明确资源集合四类数据范围。系统所有者与成员角色使用确定性默认授权；新空间写入与既有空间 Migration 使用同一权限集合，自定义角色可由企业所有者原子替换授权，系统角色不允许被改写。
- 统一策略决策点：`RbacPolicyDecisionPoint` 统一校验注册 Permission、可信工作空间、有效成员、确定性角色继承、角色权限、数据范围和 Open API Key Scope。Scope 只能缩小创建者已有权限，不能扩大角色权限；策略存储、组织树或主体事实异常时返回拒绝，高风险与关键操作不产生可复用允许缓存。
- API 安全边界：所有注册为 `authorized` 的 OpenAPI 操作在业务 Service 前按 `operation_id` 查找唯一 Permission 并执行策略决策，URL 直访或直接构造接口请求不能绕过。当前注册表扩展至 30 项 Permission 和 33 个 OpenAPI 操作；角色权限读取与替换接口分别绑定独立 Permission，页面可见性仍不构成授权。
- 数据范围消费：可信 `RequestContext` 只承载 PDP 产生的最小范围。成员、部门、岗位和成员组织读取在 Application 层继续消费范围，工作空间范围返回全量，部门树、本人或资源范围仅返回匹配数据；Repository 与后续知识检索仍必须把范围转换为工作空间约束查询，不能只依赖前端过滤。
- 数据与契约：新增 `role_permission_grants`，数据库 Revision 推进至 `20260814_0011`；复合外键关闭跨空间角色授权，Check Constraint 固定范围目标形态。OpenAPI、React/Python 类型、资源注册表、ReleaseManifest、兼容矩阵和本地诊断同步推进。
- 自动化验收：策略领域、API 绕过、角色权限协议和真实 PostgreSQL 专项通过；Migration `base → head → base → head` 结构一致。统一 `./scripts/verify` 通过，前端格式/Lint/TypeScript/测试 `3/3`/生产构建、Ruff、mypy strict、架构、契约兼容与漂移、Secret Scanner、SBOM、许可证、Manifest 和全量 pytest `217/217` 均通过。
- 容器与 HTTP 验收：API、Worker、Web 与 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、数据库 Revision `20260814_0011`、Valkey 和 Worker 八项诊断通过。使用全合成双账号完成真实 HTTP 闭环：所有者创建企业与邀请成员，建立根部门、授权子部门和范围外部门，为成员绑定部门角色并授予部门树读取；成员只读取到根与授权子部门 2 个节点，范围外部门未返回，成员清单和角色权限治理 URL 直访均返回 `403 POLICY_DENIED`。
- 当前边界：本节点只产生空 `field_mask`，字段级敏感等级、响应掩码、日志/检索/模型上下文防泄漏进入 `P1C-03`。菜单草稿、页面接口动态绑定与发布回滚仍按 `P1C-04` 至 `P1C-06` 实施；未扩展 SaaS、Go 运行层、真实连接器、LLM Grading、多模态问答、Channel Gateway 或 Durable Run。
- 提交：`1faad49`。

### P1C-03 字段级 ABAC

- 状态：通过。
- 字段策略：新增版本化 `FieldPolicyRegistry` 与 JSON Schema，固定 `PUBLIC`、`INTERNAL`、`CONFIDENTIAL`、`RESTRICTED` 四级密级，首期注册成员、知识、文档、Chunk、工作流实例和审批敏感字段。知识、工作流与审批实体仍按各自阶段创建，当前不提前建立空业务表。
- 授权模型：`RolePermissionGrant` 新增最高可读密级与显式 `field_mask`。单资源读取只合并实际覆盖该资源的授权，取最高密级并对显式遮罩求交集；集合读取采用所有数据范围中的最低密级并合并显式遮罩，避免某个范围的高密级角色放宽其他记录。未知字段名和非法密级拒绝写入；系统所有者默认 `RESTRICTED`，普通成员默认 `INTERNAL`。
- 执行边界：PDP 按资源类型、资源密级和角色授权计算 `field_mask`，存在敏感字段的结果不产生允许缓存。`RequestContext` 只承载可信决策结果；统一 `FieldProjectionService` 在 HTTP 响应、运行日志属性、检索元数据和模型上下文生成前投影，未注册资源失败关闭。
- 真实落点：成员清单接口在 Pydantic 序列化前移除受限身份字段，OpenAPI V1 保留原完整响应并新增兼容投影视图；阶段 0 检索搜索、全文精读与引用在返回前清空受限来源位置，正文被遮罩时整体拒绝；模型上下文 Builder 只接受投影后的 JSON，不向 Provider 传递原始字段。
- 数据与契约：`role_permission_grants` 新增 `maximum_security_level` 和 `field_mask`，数据库 Revision 推进至 `20260814_0012`。OpenAPI、React/Python 类型、ReleaseManifest、兼容矩阵、字段注册表 Schema 和契约兼容门禁同步更新。
- 自动化验收：字段 PDP、跨角色/跨数据范围合并、四类出口投影、成员 HTTP 序列化、检索/引用遮罩、字段注册表 Schema、RBAC 回归和 Migration 往返通过；真实 PostgreSQL 验证自定义角色字段授权持久化以及新企业 owner/member 密级种子。统一 `./scripts/verify` 通过，React 测试 `3/3` 与生产构建、Ruff、mypy strict、契约兼容与漂移、Secret Scanner、SBOM、许可证和全量 pytest `225/225` 均通过。
- 容器与 HTTP 验收：API、Worker、Web 与 Migration 镜像从当前工作树重建，八项本地诊断和数据库 Revision `20260814_0012` 通过；真实双账号 HTTP 闭环返回 2 条成员记录且每条只含 `membership_type` 与 `status`，受限 `account_id`、`display_name` 和对应原始值均未出现在响应文本中。
- 当前边界：字段注册表已为 `KnowledgeBase`、`Document`、`Chunk`、`WorkflowInstance` 和 `Approval` 固定字段语义，但正式实体与管理入口分别在 `P1D`、`P1E` 和 `P1F` 建设。当前不扩展任意脚本 ABAC、SaaS、Go 运行层、真实连接器、LLM Grading、多模态问答、Channel Gateway 或 Durable Run。
- 提交：`708c57a`。

### P1C-04 菜单页面接口统一绑定

- 状态：通过。
- 注册资源绑定：`ResourceRegistry` 推进至版本 2，冻结 32 项 Permission、5 个页面、37 个 OpenAPI 操作、35 个目录/页面/动作菜单和 30 个 `MenuApiBinding`。全部授权接口必须绑定平台注册动作菜单，动作菜单与接口使用同一 `permission_code`；GET 只接受 `query`，写接口接受受控动作类型。重复、悬空、停用、未绑定和权限码不一致均在发布前失败。
- 工作空间配置：新增 `WorkspaceMenuOverride` 和整体替换接口，个人与企业空间所有者都可调整已注册菜单的父目录、名称、图标、排序和可见性。最终合并树执行循环和父子类型校验；请求只接受 UUID、展示属性和布尔值，不能提交任意路由、组件、页面或接口地址。
- 角色菜单：新增 `RoleMenuVisibility` 和角色整体替换接口，角色可以独立隐藏目录、页面或动作入口。菜单可见性只改善前端体验，所有四个菜单管理接口和既有业务接口仍按 `operation_id`、注册 Permission、有效角色、数据范围和字段策略经过后端 PDP，直接 URL 或 API 调用不能借菜单状态绕过授权。
- 数据与事务：数据库新增静态 `registered_menu_api_bindings` 镜像、`workspace_menu_overrides`、`role_menus` 与 `workspaces.menu_version`，Revision 推进至 `20260814_0013`。配置替换、菜单版本、审计和 Outbox 同事务提交；静态契约与数据库镜像漂移时管理接口失败关闭。Migration `base → head → base → head` 保持结构一致，回滚会清理本节点系统授权种子。
- 契约与生成：OpenAPI 增加工作空间菜单读取/替换和角色菜单读取/替换；React/Python 生成类型、资源注册表 Schema、React 只读注册表、ReleaseManifest、兼容矩阵、容器诊断 Revision 和契约兼容门禁同步更新。新增 `menu_api_bindings` 保持 Schema 向后兼容可选，但当前版本化注册表领域校验要求完整集合。
- 自动化验收：资源注册表绑定反例、个人/企业配置、未知资源、循环、跨空间、角色可见性、静态镜像漂移、API 策略绕过和真实 PostgreSQL 持久化通过。统一 `./scripts/verify` 通过，React 测试 `3/3` 与生产构建、Ruff、mypy strict、架构、契约兼容与漂移、Secret Scanner、SBOM、许可证和全量 pytest `234/234` 均通过。
- 容器与 HTTP 验收：API、Worker、Web 与 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、Revision `20260814_0013`、Valkey 和 Worker 八项诊断通过。真实合成账号分别将个人和企业菜单版本推进至 2，企业所有者角色动作菜单设为不可见；伪造个人空间 Header 读取企业菜单返回 `403 POLICY_DENIED`。
- 当前边界：本节点只建立可配置覆盖层和角色可见性事实，不生成草稿、不可变发布快照、审批或回滚版本；这些进入 `P1C-05`。React 仍消费现有静态应用壳层，动态路由生成和按当前角色裁剪进入 `P1C-06`。未扩展 SaaS、Go 运行层、真实连接器、LLM Grading、多模态问答、Channel Gateway 或 Durable Run。
- 提交：`767bf39`。

### P1C-05 菜单版本与发布

- 状态：通过。
- 发布状态机：菜单发布按 `draft → validated → approved/rejected → published` 转换，校验失败保留草稿并返回问题清单，拒绝必须提供原因；已拒绝或已发布版本不能继续转换，重复发布和非法越级转换稳定拒绝。
- 不可变快照：创建草稿时冻结合并后的全部菜单、全部角色可见性、静态菜单接口绑定、`ResourceRegistry` 版本和工作空间菜单版本，并对规范 JSON 计算 SHA-256 摘要。状态更新 SQL 不包含快照或摘要字段，后续配置变更不能改写既有草稿和发布内容。
- 发布与回滚：独立 `workspace_menu_publications` 保存每个空间的当前发布指针，发布校验、状态更新和指针切换在同一事务完成，失败不会污染当前版本。回滚不改写历史，而是复制同空间已发布快照、创建新的 `rollback` 发布记录并原子切换指针。
- 治理边界：创建、校验、审批、发布、回滚和历史列表仅允许个人或企业空间所有者通过浏览器 Session 执行。活跃普通成员只可读取当前发布及完整消费快照，不能读取治理历史或执行发布动作；菜单可见性仍不替代后端 PDP，伪造空间 Header 继续失败关闭。
- 数据与契约：新增 `menu_releases` 和 `workspace_menu_publications`，Revision 推进至 `20260814_0014`。OpenAPI 新增创建、校验、审批、发布、回滚、历史列表和当前发布 7 个接口；React/Python 类型、资源注册表、ReleaseManifest、兼容矩阵和本地诊断同步更新。`ResourceRegistry` 版本 3 包含 38 项 Permission、5 个页面、44 个 `ApiResource`、42 个 Menu 和 37 个 `MenuApiBinding`。
- 自动化验收：发布领域专项 `7/7`，真实 PostgreSQL 发布与 Migration `base → head → base → head` 专项 `2/2`。统一 `./scripts/verify` 通过，React 测试 `3/3` 与生产构建、Ruff、mypy strict、架构、契约兼容与生成漂移、Secret Scanner、SBOM、许可证和全量 pytest `243/243` 均通过。
- 容器与 HTTP 验收：API、Worker、Web 与 Migration 镜像从当前工作树重建；Web、API、MinIO、Tika、PostgreSQL、Revision `20260814_0014`、Valkey 和 Worker 八项诊断通过。全合成 HTTP 场景完成个人空间首版/二版发布及回滚，回滚生成第 3 条历史记录；企业空间独立发布；企业普通成员读取当前发布返回 `200`，历史列表和创建发布返回 `403 POLICY_DENIED`，伪造个人空间 Header 读取企业当前发布同样返回 `403 POLICY_DENIED`。
- 环境说明：本地剩余磁盘不足默认 50 GB 启动门禁时，仅对本次小规模验收进程设置 `AI_PLATFORM_MIN_FREE_DISK_GB=40`；`.env.example` 与启动器默认 50 GB 基线未降低。该覆盖只证明功能可运行，不是容量、百万 Chunk 或完整并发认证。
- 当前边界：本节点提供稳定的当前发布消费契约，但 React 仍未按发布快照生成导航、路由和角色裁剪；这些进入 `P1C-06`。未扩展 SaaS、Go 运行层、真实连接器、LLM Grading、多模态问答、Channel Gateway 或 Durable Run。
- 提交：`925fa20`。

### P1C-06 动态应用壳层

- 状态：通过。
- 动态导航来源：新增 `GET /workspaces/{workspace_id}/menu-releases/current` 和企业空间当前账号有效角色读取接口。存在已发布快照时，React 只消费快照中的已注册页面菜单，并依据当前角色的 `role_menus` 可见性裁剪；页面路由、组件和图标仍由本地受控 `ResourceRegistry` 映射，服务端快照不能注入任意路由、组件或代码。
- 路由与状态：应用壳层、桌面侧栏、移动抽屉和顶部当前页面标签统一消费动态导航。刷新和深链在菜单加载完成后保持一致；当前路径未出现在有效导航时显示无权限状态；菜单接口或角色接口失败时显示加载失败状态；无发布快照的历史空间暂时回退静态注册表，保证首发前空间可用，但后端 PDP 仍独立执行授权。
- 个人与企业边界：个人空间无需额外请求角色集合，沿用所有者的后端授权；企业空间读取当前账号有效角色后裁剪角色菜单。两类空间都只能读取自身当前发布，跨空间 Header 伪造继续由后端返回 `403 POLICY_DENIED`。菜单隐藏仅改善体验，不构成接口或数据安全边界。
- 数据与契约：新增 Migration `20260814_0015`，为系统 `workspace_member` 角色补充 `authorization.effective_role.read` 的本人数据范围授权；`ReleaseManifest`、兼容矩阵、平台诊断和 Migration 往返期望同步推进至 `20260814_0015`。不修改已提交的 `0014` 历史 Migration。
- 自动化验收：动态菜单纯函数和 React 应用闭环 `6/6`；前端 Prettier、Lint、TypeScript、生产构建通过；Python/Ruff/mypy 和全量 pytest `243/243` 通过；Migration 往返 `1/1`、P1C-05 PostgreSQL 回归 `1/1`；统一 `./scripts/verify` 全部通过。
- 容器与浏览器验收：以当前工作树重建 API、Worker、Web 和 Migration 镜像，`platform doctor` 八项全部通过，数据库 Revision 为 `20260814_0015`。真实运行页面在桌面视口确认已发布企业空间显示动态菜单和页面内容；移动抽屉沿用同一导航数据源。当前未宣称 Linux 宿主机、百万 Chunk 或完整并发认证通过。
- 当前边界：本节点不实现知识库、对象存储、解析 OCR、Embedding、真实模型供应商或问答链路；这些进入 `P1D` 和 `P1E`。未扩展 SaaS、Go 运行层、真实连接器、LLM Grading、多模态问答、Channel Gateway 或 Durable Run。
- 提交：`45a5c32`。

### P1D-01 知识与文档事实模型

- 状态：通过。
- 领域事实：新增 `KnowledgeBase`、`Document`、不可变 `DocumentVersion`、`DocumentSource` 和 `DocumentPublication`。来源类型固定为 `manual`、`upload`、`web` 和 `data_source`；`data_source` 只冻结未来连接器的来源事实，不接入真实连接器。文档与知识库采用软删除，仍有活跃文档的知识库不能删除。
- 版本与发布：文档版本按 `draft → ready → published → superseded` 单向转换，已进入只读状态的版本不能改写。每个文档只有一个当前发布指针；发布新版本时，在同一事务中将旧当前版本转为 `superseded` 并原子切换指针，失败不会暴露半发布状态。
- 隔离与治理：五张业务表均以工作空间复合外键约束实体关系，Repository 查询继续携带 `workspace_id`。个人与企业空间均由所有者治理写操作；普通企业成员即使直接调用接口也返回 `403 POLICY_DENIED`。知识库支持部门范围事实，为后续检索阶段的工作空间和部门过滤提供稳定输入。
- 权限与数据安全：`ResourceRegistry` 推进至版本 4，覆盖 45 项 Permission、51 个 API、49 个菜单和 44 个菜单接口绑定。七个知识写接口全部经过统一 PDP；响应执行字段投影，不回显对象键、来源路径或 URL，避免对象定位信息提前进入 API、日志和后续模型上下文。
- 配额与事务：知识库创建和删除复用身份模块公开的原子用量能力，不直接写入权益私有表。创建时用量从 0 增至 1，删除时回到 0；配额校验、业务事实、审计记录和 Outbox 事件在共享事务中提交，失败或重放不会产生双重计数和部分写入。
- 数据与契约：新增 Migration `20260814_0016` 及 `knowledge_bases`、`documents`、`document_versions`、`document_sources`、`document_publications` 五张表。OpenAPI 新增七个知识路径和对应 Schema，React/Python 生成类型、错误目录、ReleaseManifest、兼容矩阵、Migration 往返期望与平台诊断 Revision 同步更新。
- 自动化验收：知识领域、权限、契约、配额和真实 PostgreSQL/Valkey 专项通过。统一 `./scripts/verify` 全部通过，React 测试 `6/6`、Python 全量 pytest `259/259`、Ruff、mypy strict、架构、契约兼容与生成漂移、供应链门禁均通过；Migration `base → head → base → head` 和知识/权益真实基础设施回归 `6/6` 通过。
- 容器与 HTTP 验收：以当前工作树重建 API、Worker、Web 和 Migration 镜像，`platform doctor` 八项全部通过，数据库 Revision 为 `20260814_0016`。全合成 HTTP 闭环完成个人知识创建、文档版本发布、来源敏感字段保护、知识库用量 `1 → 0`；企业所有者可创建，普通成员写入稳定返回 `403 POLICY_DENIED`。
- 当前边界：本节点只建立来源、版本和发布事实，不上传真实对象、不执行病毒扫描、解析、OCR、Embedding 或索引。对象上传和安全校验进入 `P1D-02`；真实多源连接器保持后置。未扩展 SaaS、Go 运行层、LLM Grading、多模态问答、Channel Gateway 或 Durable Run。
- 提交：`9dead1c`。

### P1D-02 对象存储与上传安全

- 状态：通过。
- 上传边界：新增创建文档上传和既有文档新版本上传两个 `multipart/form-data` 接口。服务端按 `max + 1` 有界读取，默认单文件上限为可配置的 20 MiB，不信任客户端 `Content-Length`；文件名去除路径，仅允许 TXT、Markdown、PDF、DOCX、PNG、JPEG 和 TIFF 对应扩展名。
- 类型与扫描：扩展名、声明媒体类型与真实文件签名必须一致。确定性扫描 Adapter 拒绝 EICAR、可执行文件签名、PDF 主动内容、Office 宏/ActiveX/嵌入对象、路径穿越和异常压缩比；损坏容器或扫描器无法形成可信结论时返回稳定 `503` 并失败关闭。扫描接口保持可替换，后续可接独立 ClamAV 等引擎而不修改上传用例。
- 对象存储：新增窄 `ObjectStorage` 接口和 MinIO 官方 SDK 实现，当前 Bucket 默认私有且按需创建。对象键只由服务端生成，固定为 `workspaces/{workspace_id}/uploads/{random}` 形态；客户端无法提交或读取 Bucket、对象键和来源路径。Adapter 在网络访问前验证工作空间前缀，MinIO 只是当前 S3-compatible 实现，可替换 AWS S3 或国内兼容对象存储。
- 一致性与配额：上传前先用短事务确认所有者和目标存在，再执行扫描与对象写入，避免未授权请求消耗外部资源。数据库事实写入会再次校验主体和目标；事务失败时补偿删除已写对象。`DocumentSource` 记录媒体类型、字节数、SHA-256、扫描状态、扫描器版本和时间；存储字节配额、来源事实、审计与 Outbox 在同一数据库事务提交。
- 权限与兼容：`ResourceRegistry` 推进至版本 5，保持 45 项 Permission 和 49 个菜单，新增两个 API 与既有“创建文档/创建版本”动作菜单绑定，合计 53 个 API 和 46 个绑定。OpenAPI V1 保留弃用的 `original_object_key` 可选字段以维持兼容，但任何非空值和 JSON `upload` 来源都稳定拒绝，只有受控 multipart 接口能生成对象事实。
- 数据与供应链：新增 Migration `20260814_0017`，扩展 `document_sources` 六个上传安全字段及完整性约束。引入固定 `minio 7.2.15` 与 `python-multipart 0.0.20`，OpenAPI、React/Python 类型、ReleaseManifest、兼容矩阵、SBOM、许可证清单和平台诊断同步更新。
- 自动化验收：上传检查、恶意样本、跨空间对象键、扫描不可用、授权前置、事务失败补偿、真实 MinIO put/get/delete、契约、资源注册和 Migration 往返专项通过。统一 `./scripts/verify` 全部通过，React 测试 `6/6`、Python pytest `269/269`、Ruff、mypy strict、架构、契约兼容、生成漂移和供应链门禁均通过。
- 容器与 HTTP 验收：以当前工作树重建 API、Worker、Web 和 Migration 镜像，`platform doctor` 八项通过，数据库 Revision 为 `20260814_0017`。全合成个人空间上传 58 字节 Markdown 后存储用量 `0 → 58`，响应不含对象定位信息；伪造 PDF 返回 `415 UPLOAD_TYPE_MISMATCH`，用量保持 58。
- 当前边界：本节点不创建 `IngestionJob`，不执行 Tika 解析、中文 OCR、Chunk、Embedding 或索引。确定性扫描器是可替换的本地安全基线，不宣称等同完整商业恶意文件检测；独立扫描引擎可在部署时替换。上述入库任务和解析进入 `P1D-03`，未扩展真实多源连接器、SaaS、Go 运行层、LLM Grading、多模态问答、Channel Gateway 或 Durable Run。
- 提交：`8715f6b`。

### P1D-03 入库任务、解析与中文 OCR

- 状态：通过。
- 任务事实与一致性：受控上传在保存 `DocumentSource` 的同一 PostgreSQL 事务中创建 `IngestionJob`，避免对象和文档事实已存在但任务丢失。新增 Migration `20260814_0018` 建立任务表，`20260814_0019` 以确定性任务 ID 幂等回填 `P1D-02` 已有的 clean 上传；本地三个旧上传均已对应任务。
- 状态机与并发：任务按 `queued → running → retry_wait → succeeded/failed` 转换。Worker 使用行锁、`SKIP LOCKED` 和短租约并发认领；可重试失败执行有界指数退避，默认最多三次，最后一次执行崩溃后由过期租约收敛为终止失败。任务保存失败阶段、稳定错误码和脱敏消息，不将堆栈或对象键泄漏给用户。
- 解析与对象边界：对象读取、Tika/OCR 和产物写入均在数据库事务外执行。Worker 读取来源后重新计算 SHA-256，摘要与上传安全事实不一致时不可重试地失败。确定性 JSON 产物写入 `workspaces/{workspace_id}/parsed/{version_id}/{job_id}.json`，产物不包含来源对象键。
- 中文 OCR：新增独立 `ChineseOcrAdapter`，当前由 `TikaChineseOcrAdapter` 以 Tesseract `chi_sim+eng` 实现。自定义 Tika 镜像固定 Apache Tika 基础镜像摘要、`tesseract-ocr-chi-sim` 和 Noto CJK 版本；固定字体、画布和“中国”文字生成全合成 PNG，真实 Tika 闭环验证 `ocr_used=true`、Parser 包含 `tesseract-chi-sim` 且识别结果稳定。Adapter 后续可替换 PaddleOCR，不改写任务状态机。
- 职责边界：正式 `ParseDocument` 从 Chunk 流程拆出，本节点只产生可追溯的解析 Block Artifact，不把“解析成功”误表达为“索引可用”。权限元数据 Chunk、Embedding、关键词索引和原子索引版本切换进入 `P1D-04`。
- 自动化验收：统一 `./scripts/verify` 全部通过，React 测试 `6/6`、Python pytest `276/276`，Ruff、mypy strict、架构、契约兼容与生成漂移、供应链门禁均通过；Migration 回填与 `base → head → base → head` 往返 `2/2` 通过。
- 容器与真实闭环：重建 API、Worker、Web、Migration 和自定义 Tika 镜像后，`platform doctor` 八项通过，数据库 Revision 为 `20260814_0019`，Worker 注册 `platform.outbox.dispatch.v1`、`platform.integration.consume.v1` 和 `platform.ingestion.process.v1`。Markdown 上传第一次执行产生两个 Block；中文 PNG 第一次执行完成 OCR，两类产物均不含 `source_object_key`。
- 当前边界：本节点不增加任务查询页面或手动重试 API，它们属于 `P1D-07`；不执行 Chunk、Embedding 或索引切换。未扩展真实多源连接器、SaaS、Go 运行层、LLM Grading、多模态图片问答、Channel Gateway 或 Durable Run。
- 提交：`ac9e83b`。

### P1D-04 Chunk 与索引版本切换

- 状态：通过。
- 索引事实：新增 Migration `20260814_0020`，建立持久化 `IndexVersion`、当前索引发布指针和 Chunk 追溯字段。索引版本冻结文档、来源、入库任务、解析摘要、Chunker、Tokenizer、Embedding 模型及权限元数据，按 `queued → running → retry_wait → ready → active/retired` 或 `failed` 转换；PostgreSQL 仍是任务状态唯一事实源。
- 构建与失败边界：索引 Worker 从已成功的 `IngestionJob` 幂等排队，使用行锁、`SKIP LOCKED`、短租约、最多三次执行和有界指数退避。读取 Artifact 后复核 SHA-256 与工作空间身份；格式、空 Chunk、Embedding 数量、1024 维度和有限数值均失败关闭，错误事实只保存稳定 `INDEX_*` 错误码和脱敏消息。
- 权限与追溯：结构化 Chunk 固化部门、可见性、密级和权限标签，并记录 `document_version_id`、`ingestion_job_id`、`source_id`、Parser、OCR 标记和解析摘要。关键词索引复用版本化 `cjk-bigram-v1` Tokenizer；构建期 Chunk 始终 `active=false`，因此未发布内容不会进入检索候选。
- 发布与撤权：文档发布和索引指针切换位于同一数据库事务。新文档版本已经发布但目标索引未就绪时，系统立即停用旧 Chunk，宁可暂时无结果也不暴露旧版本；目标索引完成后自动激活。同一文档版本重建会生成递增 `build_no` 并原子退役旧索引。删除文档会立即停用全部 Chunk、删除发布指针，并以 `INDEX_DOCUMENT_REVOKED` 终止在途构建。
- Embedding 边界：首期实现可替换 `EmbeddingAdapter`，本地默认 `deterministic-hash-1024-v1` 只用于确定性功能闭环和 pgvector 契约验证，不代表语义质量、真实供应商兼容性或成本验收；真实模型配置仍保持 `not_configured`。
- 自动化验收：统一 `./scripts/verify` 全部通过，React 测试 `6/6`、Python pytest `283/283`，Ruff、mypy strict、架构、契约兼容、生成漂移和开发级供应链门禁均通过；索引构建单元测试 `6/6`、PostgreSQL 发布/重建/撤权生命周期 `1/1`、知识回归和 Migration 往返合计 `7/7` 通过。
- 容器与真实闭环：重建 API、Worker、Web 和 Migration 镜像后，`platform doctor` 八项通过，数据库 Revision 为 `20260814_0020`。Worker 注册四个版本化任务并持续执行 `platform.indexing.process.v1`；本地三个历史 Artifact 均形成 `ready` 索引和完整追溯 Chunk，因文档未发布保持 `active=false`。
- 当前边界：本节点不增加检索与问答 API、任务查询页面或真实语义 Embedding 质量结论；供应商配置与密钥进入 `P1D-05`，主备路由和不可变运行配置进入 `P1D-06`，知识管理页面进入 `P1D-07`。未扩展真实多源连接器、SaaS、Go 运行层、LLM Grading、多模态图片问答、Channel Gateway 或 Durable Run。
- 提交：`052e9e1`。

### P1D-05 模型供应商与密钥

- 状态：通过。
- 平台管理边界：新增不携带工作空间的 `PlatformRequestContext` 和 `platform_administrators` 平台事实，空间所有者不会被隐式视为平台管理员。平台配置 API 逐请求复核管理员状态；本地唯一提权入口为 `./platform admin grant|revoke <login_name>`，撤权后已有 Session 的下一次请求立即失败。
- 配置与凭证：新增 OpenAI-compatible 供应商配置、不可覆盖的递增凭证版本、单一活跃凭证和独立平台审计。API Key 使用 AES-256-GCM 信封加密，关联数据绑定供应商、凭证主键和版本；响应、审计及错误均不回显明文，轮换后旧凭证原子撤销并强制重新探测。
- 外发安全：自定义 `base_url` 仅接受运维通过 `MODEL_PROVIDER_ALLOWED_HOSTS` 明确批准的公网 HTTPS 域名和 443 端口，拒绝认证信息、查询参数、片段、路径穿越、私网/保留地址及混合 DNS。能力探测连接钉住校验后的公网 IP、保留原域名 TLS 校验且不跟随重定向，并限制超时和响应读取量。
- 数据政策与运行边界：供应商默认处于 `draft`，只有数据政策已审核、声明能力全部探测通过且存在活跃凭证时才能激活。外发前继续按四级敏感级别、保留期限、训练用途和供应商位置失败关闭；后续模型网关只能通过 `resolve_runtime_access` 在调用边缘短暂取得已审核配置和明文 Key，不能直接读取凭证表。
- 数据与契约：新增 Migration `20260814_0021` 及平台管理员、供应商配置、版本化凭证和平台审计表。OpenAPI 新增供应商列表、创建、凭证轮换、数据政策审核、探测、激活和停用 7 个接口；`ResourceRegistry` 推进至版本 6，覆盖 45 项 Permission、60 个 API、49 个菜单和 46 个菜单接口绑定。新增可复现 OpenAPI 导出脚本并纳入统一漂移门禁。
- 自动化验收：统一 `./scripts/verify` 全部通过，React 测试 `6/6`、Python pytest `289/289`，Ruff、mypy strict、架构、契约兼容与生成漂移、Secret Scanner、SBOM 和许可证检查均通过；真实 PostgreSQL 供应商生命周期、Migration `base → head → base → head` 和平台管理员安全边界通过。
- 容器与 HTTP 验收：以当前工作树重建 API、Worker、Web 和 Migration 镜像，`platform doctor` 八项通过，数据库 Revision 为 `20260814_0021`。全合成平台管理员不需要 `X-Workspace-ID` 即可读取空供应商列表；执行本地撤权后，同一 Session 立即返回 `403 PLATFORM_ADMIN_REQUIRED`，验收账号最终均保持 `revoked`。
- 当前边界：未配置真实供应商，`MODEL_PROVIDER_ALLOWED_HOSTS` 默认是空列表，`provider_integration` 和 `ai_quality` 继续保持 `not_configured`。本节点只提供后端配置 API，管理页面进入 `P1D-07`；主备路由、预算、熔断、用量和不可变运行配置进入 `P1D-06`。未扩展真实多源连接器、SaaS、Go 运行层、LLM Grading、多模态图片问答、Channel Gateway 或 Durable Run。
- 提交：`0a5a86e`。

### P1D-06 模型网关与运行配置版本

- 状态：通过。
- 不可变运行配置：新增全局 `AiRuntimeConfigVersion`、按优先级排序的主备路由和独立当前发布指针。每个版本冻结系统 Prompt 及摘要、模型和供应商配置版本、能力、价格、超时、有限重试、共享熔断、输出上限与单次成本预算；数据库触发器禁止修改或删除配置和路由快照，切换及回滚只移动发布指针。
- 知识运行版本：配置同时冻结 Chunker、Embedding、索引 Schema、Reranker、检索、来源排序和安全策略版本。多源数据连接器、LLM Grading 与多模态图片问答分别只冻结 `data_source_interface`、`relevance_grader_interface` 和 `multimodal_router_interface` 版本，不实现后置能力，也不进入当前运行链路。
- 调用事实与幂等：模型调用在访问供应商前先以全局唯一 `invocation_id` 占位，并持久化运行配置、路由、供应商配置、凭证、Trace、逐次尝试、Token、成本、结束状态和稳定错误码；同一 ID 的重复请求稳定冲突，不会再次触发供应商。预算在外发前按冻结价格保守估算，最终成本按供应商 Usage 结算。
- 运行安全：每次外发都在调用边缘重新解析供应商状态、活跃凭证和数据政策，真实 Key 不进入长生命周期缓存。OpenAI-compatible 非流式 Adapter 使用已经校验并钉住的公网 IP、原域名 TLS 校验、无重定向、有限响应读取和脱敏错误分类；业务模块仍不能绕过统一网关。
- 数据与契约：新增 Migration `20260814_0022` 及运行配置、路由、当前发布、调用和尝试事实表。OpenAPI 新增运行配置列表、当前版本、创建和激活 4 个接口；`ResourceRegistry` 推进至版本 7，覆盖 45 项 Permission、64 个 API、49 个菜单和 46 个菜单接口绑定。
- 自动化验收：统一 `./scripts/verify` 全部通过，React 测试 `6/6`、Python pytest `299/299`，Ruff、mypy strict（327 个源文件）、前后端架构、契约兼容与生成漂移、Secret Scanner、SBOM 和许可证检查均通过。运行配置与模型网关专项单元测试 `8/8`、真实 PostgreSQL 配置发布/调用/防篡改闭环 `1/1`、Migration `base → head → base → head` `1/1` 通过。
- 容器与 HTTP 验收：以当前工作树运行 Web、API、Worker、PostgreSQL、Valkey、MinIO 和 Tika，`platform doctor` 八项通过，数据库 Revision 为 `20260814_0022`。全合成平台管理员不携带 `X-Workspace-ID` 可读取空供应商列表、空运行配置列表和空当前版本；撤权后同一 Session 立即返回 `403 PLATFORM_ADMIN_REQUIRED`，账号最终保持 `revoked`。
- 当前边界：真实供应商仍为 `not_configured`，运行网关只以 Mock Provider 和全合成数据完成功能验收，不能据此宣称真实模型兼容性、质量或成本通过。管理页面进入 `P1D-07`；未扩展真实多源连接器、SaaS、Go 运行层、LLM Grading、多模态图片问答、Channel Gateway 或 Durable Run。
- 提交：`626a27c`。

### P1D-07 知识生产与模型配置页面

- 状态：通过。
- 知识生产工作台：新增知识库侧栏、文档与入库任务双视图，支持创建知识库、首版上传、上传新版本、解析状态跟踪、确认就绪、发布版本和暂时性失败人工重试。页面只展示当前工作空间和统一 PDP 已授权范围内的事实，错误、空数据、处理中和不可重试状态均有独立呈现。
- 状态同步与重试边界：新增知识库、文档和任务范围化查询应用服务；人工重试仅接受稳定的暂时性错误，重新打开最多 3 次的有限尝试窗口，并记录次数、操作者、时间、审计与 Outbox。永久内容错误要求上传新版本；已删除知识库或文档不能通过旧任务 ID 恢复。Worker 独立更新任务和文档事实时，前端随每个任务快照刷新文档读模型，真实浏览器验收发现并关闭了过期 `draft` 快照触发 `409` 的竞态。
- 平台模型治理页面：新增供应商与运行配置双视图，覆盖自定义 `base_url`、API Key 创建与轮换、数据政策审核、能力探测、启停供应商、主备路由、超时、重试、熔断、成本及知识组件版本配置和不可变版本发布。平台页面使用独立管理员守卫，不进入工作空间菜单草稿、角色菜单或发布快照；后端仍逐请求复核管理员事实。
- 菜单、数据与契约：新增 Migration `20260814_0023` 和入库任务人工重试字段及完整性约束；`ResourceRegistry` 推进至版本 8，覆盖 50 项 Permission、7 个页面、68 个 API、67 个菜单和 61 个菜单接口绑定。OpenAPI、React/Python 类型、ReleaseManifest、兼容矩阵、平台诊断和 Migration 往返期望同步推进；旧菜单发布快照作为当前注册表的合法子集继续可读和可回滚，不自动注入新页面。
- 自动化验收：统一 `./scripts/verify` 全部通过，React 测试 `11/11`、Python pytest `306/306`，Ruff、mypy strict（330 个源文件）、前后端架构、契约兼容与生成漂移、Secret Scanner、SBOM、许可证和生产构建均通过。知识管理、授权注册表、启动兼容等非数据库专项 `38/38`，真实 PostgreSQL 知识生命周期与 Migration `base → head → base → head` `7/7` 通过。
- 容器与浏览器验收：重建 Web、API、Worker 和 Migration 镜像后，`platform doctor` 八项通过，数据库 Revision 为 `20260814_0023`。个人空间所有者通过全合成 Markdown/TXT 完成知识库创建、V1 上传解析发布、V2 上传后状态自动同步和发布，两个入库任务均成功；普通企业成员访问知识生产被后端拒绝。非平台管理员直访模型页面显示拒绝，临时授予后页面和配置表单可用，撤权后同一 Session 立即恢复拒绝，合成账号最终保持 `revoked`。
- 响应式验收边界：本轮真实浏览器完成桌面布局检查且未发现横向溢出或控件遮挡；当前内置浏览器的视口覆盖能力未实际改变 1280 像素 CSS 视口，因此本节点不宣称新增页面已完成真实 390 像素截图验收。React 组件、生产构建和响应式样式检查已通过，完整移动端与可访问性验收保留在 `P1G-04`。
- 当前边界：真实供应商、模型质量与成本仍为 `not_configured`；真实多源连接器、SaaS、Go 运行层、LLM Grading、多模态图片问答、Channel Gateway 和 Durable Run 均未扩展。
- 提交：待本节点独立提交。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、质量、成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机兼容和生产容量结论。
- 当前没有真实企业客户，阶段 1 使用固定的合成企业空间完成产品与安全验收。
- 镜像扫描未获外发授权，状态为 `not_configured`；正式发布门禁已明确失败，不影响后续本地 MVP 功能节点。

## 5. 阶段结论

`not_run`。阶段 0 已关闭，阶段 1 已完成至 `P1D-07`，当前进入 `P1E-01`。
