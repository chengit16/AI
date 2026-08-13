# 阶段 1 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 1：工作空间、企业治理与知识问答 MVP |
| 状态 | 进行中 |
| 报告日期 | 2026-08-14 |
| 当前节点 | `P1C-05` 待开始 |
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
- 提交：本提交。

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
- 提交：本提交。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、质量、成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机兼容和生产容量结论。
- 当前没有真实企业客户，阶段 1 使用固定的合成企业空间完成产品与安全验收。
- 镜像扫描未获外发授权，状态为 `not_configured`；正式发布门禁已明确失败，不影响后续本地 MVP 功能节点。

## 5. 阶段结论

`not_run`。阶段 0 已关闭，阶段 1 已完成至 `P1C-04`，当前进入 `P1C-05`。
