# 阶段 2 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 2：可靠性、数据治理与运营增强 |
| 状态 | 已完成 |
| 报告日期 | 2026-08-16 |
| 当前节点 | `P2-12` 阶段验收与关闭已完成 |
| 阶段可靠性 | `passed` |
| 阶段 1 `core_functional` | `passed`，继承标签 `stage-1-complete` |
| `provider_integration` | `not_configured` |
| `ai_quality` | `not_configured` |
| `capacity_certification` | `not_run` |
| Linux 宿主机验收 | `not_run` |

## 2. 继承环境

| 项目 | 当前值 |
| --- | --- |
| 主要开发环境 | macOS 26.5.2，Apple Silicon `arm64`，18 GB 内存 |
| Node.js / pnpm | 24.19.0 / 11.20.0 |
| 项目 Python | 3.12.12，由 uv 管理 |
| 容器运行时 | Docker Desktop 4.86.0，Docker Engine 29.7.2，Compose v5.3.1 |
| 数据库基线 | PostgreSQL 16，当前开发 Revision `20260815_0041`；阶段 1 发布仍冻结在 `20260815_0035` |
| 阶段 1 发布 | 本地 MVP `0.1.0`，ReleaseManifest 摘要 `e8983e87…b62943` |
| 阶段 2 发布 | 本地可靠性版本 `0.2.0`，ReleaseManifest 摘要 `17ae80ee…7b245d` |
| 数据与模型 | 只使用版本化合成数据；默认 Mock Provider，不代表真实 AI 质量 |

## 3. 节点记录

按 [`阶段 2 实施计划`](./stage-2-plan.md) 持续追加每个节点的完成日期、实现边界、测试计数、故障演练、容器/浏览器结果、已知限制和 Git SHA。未实际执行的检查保持 `not_run` 或 `not_configured`。

### P2-01 可靠性契约、指标与故障注入基线

- 状态：已完成，提交 `9da73a9`。
- 契约边界：新增 `p2-01-v1` 可靠性基线及两份 Draft 2020-12 JSON Schema，冻结 10 条跨任务、索引、SSE、权限、审计、Outbox、生命周期和可观测性不变量；PostgreSQL 继续拥有业务与 SSE 事实写入权，通知、缓存和派生索引不能反向覆盖事实。
- 指标与口径：冻结 10 项指标的聚合方式、阈值、单位、事实来源、测量窗口和失败条件，包括入库恢复 `p99 <= 180s`、重复副作用比例 `= 0`、索引引用不一致 `= 0`、跨实例 SSE 恢复 `p99 <= 5s`、撤权传播 `p99 <= 5s`、Outbox 最老积压 `max <= 300s`、高风险审计覆盖率 `= 1`、删除完成 `p99 <= 3600s`、恢复校验失败 `= 0` 和关键 Trace 覆盖率 `>= 0.99`。百分位统一使用 nearest-rank 且至少 20 个样本，样本不足保持 `not_run`，失败和超时样本不得剔除。
- 保留期与授权：SSE 事件/游标固定 24 小时，Job Attempt 与 Outbox Dead Letter 固定 90 天，已投递 Outbox 固定 30 天，最小化审计/用量与删除证明固定 365 天；撤权必须传播到菜单、接口、检索和字段投影，通知异常时失败关闭，旧缓存必须拒绝并重新校验 PostgreSQL 策略版本。
- 故障数据：新增 `p2-01-v1` 全合成 Fixture，覆盖超时、重复、乱序、部分失败、依赖不可用、恢复完整性、权限传播和可观测性泄漏 8 类、12 个场景。每个场景固定前置条件、注入点、步骤、终态、错误码、恢复上限、安全不变量、证据和清理方式；当前只冻结契约与数据，真实故障执行将在 `P2-02`～`P2-11` 分节点接入，未执行项不据此判定通过。
- 自动验收：专项与契约测试 `32/32`；统一门禁 React `36/36`、Python `446/446`、Ruff、mypy strict `428` 个源文件、OpenAPI/契约兼容、模块依赖、注释、UnoCSS、Secret Scanner、SBOM、ReleaseManifest 和生产构建全部通过。`./platform doctor` Web、API、MinIO、Tika、PostgreSQL、Revision `20260815_0035`、Valkey 和 Worker 八项通过。

### P2-02 任务阶段、尝试历史与恢复机制

- 状态：已完成，提交 `08385b4`。
- 事实模型：保留 `ingestion_jobs` 作为 PostgreSQL 唯一任务事实源，新增 `ingestion_job_stages` 保存当前阶段运营状态，新增 `ingestion_job_attempts` 只追加每次租约执行；没有创建第二套队列或引入新消息中间件。阶段 1 既有任务在升级时生成明确标记的 `legacy_backfill` 汇总 Attempt，不伪造缺失历史。
- 状态与恢复：增加稳定 `cancelled`、`timed_out` 终态；自动尝试仍受 `max_attempts` 约束，人工恢复最多 3 次且每次开启新 generation、Attempt 序号从 1 重新计算。租约过期先冻结旧 Attempt，再进入有限恢复或稳定超时；终态 Attempt 的更新和删除均由 PostgreSQL 触发器拒绝。
- 并发与幂等：活动租约同时绑定 `worker_id` 和不可复用 `active_attempt_id`，同名 Worker 的旧执行不能覆盖新租约；即使回收扫描尚未运行，超过 `claim_until` 的成功或失败回写也会立即失租。任务、阶段与 Attempt 在同一事务转换，重复完成不会新增历史或覆盖终态。
- 取消与审计：排队、运行和等待重试任务可转换为取消终态；运行中取消必须恰好关闭一个活动 Attempt，任务、阶段、Attempt、审计记录及 Outbox 事件同事务提交。取消后的迟到 Worker 回写被拒绝，成功、失败、取消和超时终态不能再次取消。
- 契约与兼容：OpenAPI V1 兼容性新增 `cancelled/timed_out` 状态以及可选 `can_cancel/cancelled_at` 字段，React 与 Python 生成类型同步；知识生产页能区分失败、超时和取消，不提前建设 `P2-10` 的运营取消界面。兼容矩阵同时允许阶段 1 的 `0035` 和当前开发 `0036`，阶段 1 ReleaseManifest 与恢复证据继续固定在 `0035`。
- 故障与 Migration：真实 PostgreSQL 覆盖自动重试独立 Attempt、旧租约迟到、租约耗尽、`timed_out -> manual_recovery -> cancelled`、不可变触发器、审计/Outbox 原子性及重复完成；Migration 覆盖空库 `base -> head -> base -> head`、非空升级、取消终态降回阶段 1 可理解失败态及结构一致性。
- 自动验收：专项单元与既有入库回归 `13/13`，P2-02 PostgreSQL 和 Migration `6/6`；统一门禁 React `37/37`、Python `457/457`、Ruff、mypy strict `431` 个源文件、OpenAPI/契约兼容、模块依赖、注释、UnoCSS、Secret Scanner、SBOM、ReleaseManifest 和生产构建全部通过。`./platform doctor` Web、API、MinIO、Tika、PostgreSQL、Revision `20260815_0036`、Valkey 和 Worker 八项通过。

### P2-03 Worker 隔离、索引死信与安全并发

- 状态：已完成，提交 `0700701`。
- 进程与队列：Celery Beat 从业务 Worker 中拆出独立 Scheduler；control、parsing、OCR、embedding 和 indexing 五个 Worker 进程分别只监听 `platform.control`、`platform.parsing`、`platform.ocr`、`platform.embedding` 和 `platform.indexing`。TXT、Markdown、DOCX 进入 parsing，PDF 与图片进入 OCR；PostgreSQL 认领条件再次复核 Lane，Broker 不能决定业务事实。
- 索引阶段：原索引构建拆为 Embedding 和 Indexing 两个真实阶段。Embedding 在事务外复核 Artifact、生成 Chunk 与向量并写入 `active=false` 的不可见构建；Indexing 只提交已完整落库的构建，再原子切换索引版本与发布指针。任一阶段失败或并发重放都不能让半成品可见。
- 死信与恢复：索引租约绑定不可复用 `active_attempt_id`，过期租约、同名 Worker 迟到结果和旧 Attempt 均失去提交权。自动尝试耗尽后进入稳定 `dead_letter`，人工恢复最多开启 3 个新 generation；终态 Attempt 由数据库触发器保持不可变，重复恢复不能重复发布或覆盖当前索引。
- 并发与配置：五个 Lane 并发可分别配置，应用层统一限制为 `1～8`，本地默认 control/parsing/OCR/embedding/indexing 为 `2/2/1/1/2`。Compose 补齐批量、租约、退避、扫描周期、文件/页数和 Chunk 参数，PostgreSQL 继续作为唯一事实源，Valkey 只承担 Broker 与唤醒。
- 故障隔离演练：停止 OCR Worker 后，Scheduler 继续向各队列投递，OCR 队列独立积压到 11 条，parsing、embedding 和 indexing 队列均保持为 0；control、parsing、embedding 和 indexing 日志持续记录任务成功，未出现跨 Lane 消费。通过统一入口恢复 OCR Worker 后积压归零，五个 Lane 继续成功执行。
- 健康与密钥：演练将 32 字节任务签名密钥校验纳入五个 Worker 健康检查，避免 Celery 节点可响应但任务无法装配的假健康；Scheduler 不挂载文件型密钥并具有独立 Beat 健康检查。启动器按 12 个常驻服务等待就绪，`./platform doctor` 实际检查 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、五个 Worker 和 Scheduler 共 13 项。
- 自动验收：Worker 路由与并发专项 `18/18`，P2-02/P2-03 PostgreSQL 与 Migration `9/9`，检索 PostgreSQL 回归 `1/1`；统一 `./scripts/verify` 通过 React `37/37`、Python `473/473`、Ruff format/lint `433` 个文件、mypy strict `433` 个源文件、模块依赖、中文注释、UnoCSS、OpenAPI/契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建。最终 `./platform start` 正常返回，Revision `20260815_0037` 和 13 项容器诊断全部通过。

### P2-04 索引巡检、差异修复与全量重建

- 状态：已完成，实现提交 `8fa9e90`。
- 巡检事实：新增 `index_maintenance_runs` 和 `index_inspection_findings`，以 PostgreSQL 文档发布、成功入库、索引版本、索引发布和 Chunk 引用为唯一事实，覆盖发布缺失/版本错配、索引状态、来源摘要、Chunk 数量/引用、多余活动 Chunk 和孤儿活动版本 8 类稳定差异码；巡检证据不保存正文或向量。
- 安全修复：每次修复重新锁定当前文档发布，过期报告不能恢复历史版本。相同当前文档版本存在完整 `ready/active/retired` 候选时原子恢复；没有候选时先撤销异常活动面，再从成功入库事实幂等排队新构建；没有成功来源时保持 `unresolved`，不伪造已排队结论。
- 重建与清理：全量重建只扫描活动文档的当前发布版本，使用稳定维护运行 ID 保证重放不重复建构；健康旧索引在新构建完整提交前继续服务，失败和 staged Chunk 始终不可见。清理只删除永久失败或三次人工恢复耗尽版本的不可见 Chunk，摘要由实际删除 Chunk ID 生成，仍可恢复死信和活动发布保持不变。
- 调度与边界：注册 `platform.indexing.inspect.v1` 并固定进入 `platform.indexing` Lane，Scheduler 默认每 300 秒投递，配置边界为 `60～86400` 秒。全量重建和清理已具备幂等应用层，人工权限、确认和审计入口仍按计划留给 `P2-10`，当前不提供绕过治理的直接改表操作。
- 自动验收：P2-04 PostgreSQL 场景 `5/5`，覆盖健康零差异、损坏活动索引切换完整旧候选、无候选撤销并重建、全量重建重放幂等及恢复预算清理；统一 `./scripts/verify` 通过 React `37/37`、Python `480/480`、Ruff format/lint `438` 个文件、mypy strict `438` 个源文件、Migration 往返、模块依赖、中文注释、UnoCSS、OpenAPI/契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建。
- 容器验收：`./platform start` 正常完成现有数据 `0037 → 0038` 升级，`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、五个 Worker 和 Scheduler 共 13 项通过。真实 Celery 投递 `platform.indexing.inspect.v1` 后由 Indexing Worker 写入 `inspection|1|0|0|0`，证明扫描 1 份已发布合成文档且无差异、修复或重建。

### P2-05 跨实例 SSE 通知与恢复

- 状态：已完成，实现提交 `0319abc`，恢复预算修正提交 `52dcf4b`。
- 架构决策：接受 [`ADR-005`](../decisions/ADR-005-cross-instance-sse-wakeup.md)，采用 Valkey Pub/Sub 无正文唤醒与 PostgreSQL 有界轮询兜底，不引入 Valkey Streams。PostgreSQL 继续唯一保存 Run、事件、严格序号、游标保留期和最终快照；通知频道只包含版本前缀与 Run UUID，载荷固定为 `1`，不能成为事实或直接返回客户端。
- 提交与恢复：创建 Run、追加事件和写入终态都在数据库事务成功提交后才尽力发布通知。SSE 连接先订阅目标 Run，收到通知后仍按可信工作空间和 `Last-Event-ID` 回查 PostgreSQL；通知丢失、订阅失败、Valkey 不可达或连接中断时，继续按默认 250 毫秒、配置上限 4 秒的周期轮询，为 5 秒恢复门禁保留查询与传输余量，不重新创建 Run，不持有长数据库事务。
- 安全与顺序：两个独立服务实例共享事实库和通知层时，读实例只得到原 Run 的严格递增事件；关闭写实例后，替代实例可从原事件 ID 继续回放且 `last_sequence_no` 保持不变。跨工作空间回放失败关闭，同会话第二个活动 Run 被拒绝；`message.snapshot` 和 24 小时事件/游标语义未改变，OpenAPI/SSE V1 无破坏性变化。
- 故障与时限：真实 PostgreSQL/Valkey 专项 `4/4`，覆盖跨实例唤醒与替代实例恢复、全部通知丢失后的数据库轮询、20 个跨实例恢复样本和 Valkey 不可达。20 个样本按 `P2-01` nearest-rank 口径均在 `p99 <= 5s` 门禁内；500 条 SSE 条件容量认证仍为 `not_run`，实际双 API 进程停止与联合故障演练继续由 `P2-11` 执行。
- 自动验收：P2-05 单元 `6/6`，相关 SSE/应用回归 `27/27`；统一 `./scripts/verify` 通过 React `37/37`、Python `490/490`、Ruff format/lint `441` 个文件、mypy strict `441` 个源文件、模块依赖、中文注释、UnoCSS、OpenAPI/契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建。`./platform start` 重建真实 API 装配后，`./platform doctor` 的 13 项诊断全部通过，数据库 Revision 保持 `20260815_0038`。

### P2-06 结构化日志、Trace、指标与健康告警基线

- 状态：已完成，提交 `9ea06f2`。
- 字段安全：新增版本化可观测字段注册表，对日志、Span、Prometheus 标签和告警事实执行“未登记即拒绝”。凭据、Cookie、Session、正文、查询、Prompt、模型输入输出、事件 Payload、字段级 ABAC 受限值及用户/空间/资源等高基数标识不得进入普通可观测通道；第三方自由文本日志统一收敛为不复制原始消息的安全 JSON 事件。
- Trace 传播：FastAPI 在 HTTP 边缘校验 W3C `traceparent` 并建立请求 Span，应用层通过标准 OTel Context 关联检索、模型、Assistant、工作流、审批和 Outbox。五个 Worker Lane 均记录任务 Span、结果与耗时，集成事件只有在 HMAC 信封验签成功后才能恢复父 Trace，未验证 Header、Broker 属性和任务载荷不能成为可信父链。
- 指标与聚合：新增 HTTP、应用操作、Worker 任务、依赖健康、Outbox 积压/重试/死信和 SSE 发布/订阅/轮询/回查指标；HTTP 只使用路由模板，不使用真实资源路径。API 与 Worker 通过 `.ai-platform/runtime/prometheus` 共享多进程指标文件，启动前清理旧运行数据，子进程退出时清理 live Gauge；`/api/v1/metrics` 实际同时采集到 API 请求和五个 Worker Lane 的成功任务样本。
- 健康与告警：Readiness 保留既有 `checks` 兼容字段并增加检查时间、延迟、关键性和稳定原因码，Web 运行状态页同步展示这些事实。Prometheus 规则覆盖关键依赖不可用、HTTP 错误比例/P95 延迟、Worker 失败、Outbox 最老积压/死信和 SSE 通知失败；本地未部署 Prometheus、Alertmanager 或 Trace 后端，规则和 OTLP 接口已就绪但不冒充外部监控链已投产。
- 安全与覆盖验收：20 条合成关键 Trace 均覆盖 HTTP、任务、检索、模型、工作流、审批和 Outbox，覆盖率 `1.0`，达到 `>= 0.99` 门禁。专项 `9/9` 覆盖字段 Schema、生产环境 HTTPS OTLP、日志/Span/指标/告警四通道敏感数据拒绝、安全第三方 Formatter、多进程 Worker 聚合和告警规则解析。
- 自动验收：统一 `./scripts/verify` 通过 React `37/37`、Python `500/500`、Ruff format/lint `447` 个文件、mypy strict `447` 个源文件、模块依赖、中文注释、UnoCSS、OpenAPI/契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建；P2-06、真实 PostgreSQL/Valkey SSE 及 Outbox 专项 `18/18`。`./platform start` 完成 API/Worker 镜像重建，数据库 Revision 保持 `20260815_0038`，`./platform doctor` 的 13 项诊断全部通过。

### P2-07 审计、用量、配额与 Outbox 运营

- 状态：已完成，实现提交 `ac10043`。
- 审计追溯：审计事实补齐实际授权权限码、策略决策 ID 和策略版本，使主体、工作空间、资源、操作、授权依据、结果、Request 与 Trace 能形成稳定关联。运营响应返回固定追溯字段，不复制自由属性或业务内容。
- 用量账本：既有原子计数器增加不可变用量明细分页与逐计量项/周期对账；每条记录保存幂等键、变更量、变更后值和发生时间。对账同时比较计数器、明细累计和最后结果，任何差异保持 `consistent=false`，不自动改写或掩盖账本。
- Outbox 运营：提供工作空间隔离的事件状态查询，响应不包含 Payload；巡检汇总四类状态、最老积压、过期租约、不兼容事件 Schema、重放请求、消费回执、重复接收和幂等异常。消费回执增加 `delivery_count` 与 `last_received_at`，相同事件重复送达只更新回执，不再次执行投影。
- 安全重放：只允许具有 `operations.outbox.replay` 的浏览器主体重放 `published/dead_letter` 事件，API Key 与无用户主体失败关闭。客户端幂等键在工作空间内唯一；重放保留原 `event_id`，不可变请求事实、事件重新排队和带结构化原因的审计记录同事务提交，重复调用返回同一请求。
- 权限与契约：资源注册表升至 16，包含 78 个权限、107 个 API、95 个菜单和 100 个菜单接口绑定；`operations.records.read` 与 `operations.outbox.replay` 仅默认授予个人/企业 `workspace_owner`，并为既有空间升级当前菜单快照。OpenAPI、React/Python 生成类型、错误目录和兼容矩阵同步，阶段 1 发布清单仍冻结在 Revision `0035`。
- Migration 与回归：Revision `20260815_0039` 增加审计追溯列、消费回执接收计数、不可变 `outbox_replay_requests`、工作空间复合外键和运营查询索引，完成既有 Owner 权限回填及菜单快照升级。空库与非空库往返 `4/4`、P2-07 HTTP `2/2`、真实 PostgreSQL `5/5`、Outbox/权益/P2-07 联合回归 `20/20` 通过。
- 自动验收：统一 `./scripts/verify` 通过 React `37/37`、Python `508/508`、Ruff format/lint `456` 个文件、mypy strict `456` 个源文件、Migration、模块依赖、中文注释、UnoCSS、OpenAPI/契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建。`./platform start` 完成镜像重建和现有数据升级，数据库 Revision 为 `20260815_0039`，`./platform doctor` 的 13 项诊断全部通过。

### P2-08 工作空间数据导出、清除与保留期执行

- 状态：已完成，实现提交 `78fbf2c`。
- 架构决策：接受 [`ADR-006`](../decisions/ADR-006-workspace-data-lifecycle.md)，工作空间导出使用独立 `workspace-export.v1` ZIP/JSONL，不复用整实例 `.aiprb` 恢复包；个人版和企业版统一采用“保留空间治理壳层、清除业务数据”的语义，避免个人空间删除后无法登录，也不把工作空间级导入提前纳入当前范围。
- 导出完整性：导出按稳定顺序生成规范化 Manifest、表级 JSONL 和对象清单，记录行数、大小与 SHA-256，可重复校验数据库事实和 MinIO 对象；凭据摘要、密码、Session、平台密钥和供应商密钥不进入包。工作空间表注册表对全部含 `workspace_id` 的表执行显式分类，修复两张检索依赖表的分类后，未登记新表会由集成测试失败关闭。
- 清除与证明：只有具有独立权限的浏览器 Owner 可提交空间名称确认、结构化原因和幂等键；API Key 不能触发。流程清除业务事实、旧可删除事件、MinIO 工作空间对象和 Valkey 派生键，同时保留治理壳层、最小审计、用量和生命周期事实；完成后追加不含业务正文的不可变删除证明，外部存储失败保持可重试。
- 保留策略：按冻结口径执行 SSE 事件 24 小时、已投递 Outbox 30 天、Job Attempt 与 Outbox Dead Letter 90 天、最小审计/用量/删除证明 365 天。边界时间和实际删除计数进入保留期运行事实，不复制已删除正文；普通事务仍受不可变触发器保护，只有生命周期 Repository 可在事务内开启受限清理旁路。
- 并发与幂等：导出、清除和保留期接口均绑定工作空间级幂等事实；同键同请求复用已有结果，不同请求内容复用同键被拒绝。并发保留期执行只产生一个运行事实，竞争请求在首个事务未完成时返回 `running`，不会把未完成结果误报为成功或重复删除。
- 权限与契约：资源注册表升至 17，包含 81 个权限、110 个 API、98 个菜单和 103 个菜单接口绑定；新增导出、清除和保留期执行三项 Owner 权限及 HTTP 接口。OpenAPI、React/Python 生成类型、兼容矩阵和既有空间菜单快照同步，阶段 1 ReleaseManifest 继续冻结在 Revision `0035`。
- Migration 与专项：Revision `20260815_0040` 增加导出、清除、保留期运行和不可变删除证明事实，受限调整不可变触发器，并完成既有 Owner 权限和当前菜单快照升级。P2-08 单元、API 与真实 PostgreSQL 专项 `8/8` 通过，覆盖确定性导出、凭据排除、跨空间隔离、清除完整性、普通事务保护、边界保留期和并发幂等；Migration 往返与既有生命周期依赖回归通过。
- 自动与容器验收：统一 `./scripts/verify` 通过 React `37/37`、Python `516/516`、Ruff format/lint `475` 个文件、mypy strict `475` 个源文件、Migration、模块依赖、中文注释、UnoCSS、OpenAPI/契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建。`./platform start` 完成镜像重建和现有数据升级，数据库 Revision 为 `20260815_0040`，`./platform doctor` 的 13 项诊断全部通过；真实合成个人空间通过 HTTP 完成导出、保留期和业务数据清除，三项运行状态均为 `completed`。

### P2-09 权限传播与异常访问

- 状态：已完成，实现提交 `fbbae62`。
- 版本事实：PostgreSQL `workspaces.role_version` 继续作为唯一策略版本事实，Valkey 只保存可丢失、可重建的版本见证，不取得授权事实写入权。Lua 原子比较支持缺失见证自举和一致版本命中；缓存旧版时先刷新见证但拒绝当前请求，未来版、损坏、协议异常、非法来源版本和 Valkey 不可用均失败关闭。
- 统一授权表面：`PolicyRequest.surface` 固定区分菜单、API、检索和字段投影，生产 PDP 在解析当前 PostgreSQL 主体版本后统一经过版本门禁。版本异常形成零缓存时长的 `policy_unavailable` 拒绝决策，HTTP 边缘映射为可重试 `503 POLICY_UNAVAILABLE`；OpenAPI V1 与 React 生成类型同步增加兼容响应声明。
- 前端传播：菜单发布和企业有效角色每 2 秒独立刷新，在正常网络下为 5 秒传播门禁保留余量。菜单、角色或工作空间事实任一查询异常时立即清空历史导航和按钮权限集合，桌面侧栏、移动抽屉与路由守卫不能继续使用旧快照或静态回退；接口和字段安全仍由后端 PDP 独立执行。
- 指标与告警：新增授权决策和策略缓存 Counter，只使用服务、环境、固定表面、结论、稳定原因码和缓存状态等低基数标签，不记录权限目标、主体、工作空间、资源标识或受限正文。Prometheus 规则覆盖策略不可用、集中越权探测和异常缓存见证，本地仍只验证规则与指标事实，不冒充外部 Alertmanager 已部署。
- 故障与时限：真实 PostgreSQL/Valkey 演练在撤销自定义角色绑定后持续写回旧版本见证，20 个样本轮换覆盖四个授权表面；全部样本先返回 `policy_unavailable`，nearest-rank `p99 <= 5s`，见证修复后按新 PostgreSQL 事实稳定返回 `permission_not_granted`。拒绝结果和指标不包含合成受限正文，跨空间和旧权限均未暴露。
- 自动与容器验收：P2-09 单元 `10/10`、真实 PostgreSQL/Valkey `1/1`，P2-09/API/可观测联合回归 `26/26`；统一 `./scripts/verify` 通过 React `38/38`、Python `529/529`、Ruff format/lint `478` 个文件、mypy strict `478` 个源文件、模块依赖、中文注释、UnoCSS、OpenAPI/生成契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建。`./platform start` 重建真实 API/Web/Worker 镜像，数据库 Revision 保持 `20260815_0040`，`./platform doctor` 的 13 项诊断全部通过。

### P2-10 受控运营工作台

- 状态：已完成，实现提交 `0f20d17`。
- 统一读模型：运行状态页增加任务、索引、Outbox、审计与用量、生命周期五个运营页签和低基数摘要；工作台读取绑定可信工作空间，只返回登记字段，不把事件 Payload、任务异常正文、文档正文或受限字段送入浏览器。无读取权限时不发起运营请求，查询失败时清空旧数据并统一显示失败状态。
- 受控命令：新增入库任务取消，以及索引巡检、全量重建和失败索引清理入口。三类索引命令只接受浏览器主体，必须同时提供结构化原因码、命令专属固定确认词和 `Idempotency-Key`；重复同请求返回原事实，复用键改变命令内容会被拒绝，请求、授权审计和最小 Outbox 事件在同一事务提交。
- Worker 闭环：新增 `index_maintenance_requests` 作为人工命令请求事实，Indexing Worker 以租约批量认领并继续复用 P2-04 维护应用层；请求 ID 同时作为维护运行 ID，重放不会生成第二份运行证据。单条失败不阻塞同批其他请求，错误收敛为稳定错误码，最多 3 次有限重试后进入 `dead_letter`。
- 权限与兼容：资源注册表升至 18，包含 85 个权限、119 个 API、102 个菜单和 112 个菜单接口绑定；新增 4 项 Owner 默认权限和 9 个 API 绑定，并为既有空间复制当前菜单发布快照，历史快照保持不变。OpenAPI、React/Python 生成类型、工作空间表注册表和兼容矩阵同步，阶段 1 ReleaseManifest 继续冻结在 Revision `0035`。
- 数据库与自动验收：Revision `20260815_0041` 增加索引维护请求、租约、有限重试、死信和工作空间约束，Migration 往返及历史菜单升级通过。P2-10 单元 `4/4`、真实 PostgreSQL `3/3`；统一 `./scripts/verify` 通过 React `42/42`、Python `536/536`、Ruff format/lint `495` 个文件、mypy strict `495` 个源文件、模块依赖、中文注释、UnoCSS、OpenAPI/生成契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建。
- 容器与浏览器验收：`./platform start` 重建当前 API、Web、Worker 和 Migration 镜像，数据库 Revision 为 `20260815_0041`，`./platform doctor` 的 13 项诊断全部通过。全合成个人空间 Owner 在真实页面登记索引巡检，Worker 完成后请求状态、一次尝试和可复算摘要正常呈现；`1440×900` 与 `390×844` 下摘要、五个页签、空态和危险操作弹窗可用，移动端页面宽度稳定为 390px，宽表格只在 328px 局部容器滚动，长确认词正确换行且控制台无错误。

### P2-11 联合故障演练

- 状态：已完成，实现提交 `878f7ef`。
- 清单与契约：新增 `p2-11-v1` 全合成演练清单，以及独立的清单和证据 Draft 2020-12 JSON Schema。清单冻结 PostgreSQL、MinIO、对象快照、派生索引、Worker 租约、API 实例、SSE 通知、Valkey、Outbox 和授权 10 类组件、12 个唯一 pytest 节点，并校验 P2-01 场景、不变量、真实文件和函数 AST 引用；JSON 不具备执行任意命令的能力。
- 执行与失败关闭：新增 `./platform drill-stage-2`。入口先执行 13 项 `doctor`，健康后一次性运行固定节点并解析 JUnit，最后无条件再次诊断；前置失败不运行场景，场景失败、缺失、跳过、无效 JUnit、pytest 非零退出或恢复诊断失败都会令整次演练失败。证据原子写入忽略目录，只包含清单摘要、Git Revision、工作区状态、时间、耗时、状态和稳定原因码，不包含 stdout、stderr、异常正文、业务载荷或凭据。
- 联合覆盖：数据库逻辑恢复和 Migration 往返、MinIO 引用缺失检测、对象快照非空目标保护、异常索引撤销与事实重建、Worker 过期租约和迟到结果、跨 API 实例原 Run 恢复、SSE 通知丢失轮询、Valkey 不可用事实提交、Outbox 积压租约恢复与重复消费幂等、恢复后身份边界及四表面撤权均在同一入口执行。
- 回归发现与修复：首次真实运行通过 `11/12` 并在恢复后保持 13 项健康，唯一失败暴露 `AI_PLATFORM_DATABASE_URL` 会覆盖程序化恢复测试指定的临时数据库，使 Alembic 错连主库。Migration 环境增加仅供可信程序化调用的显式数据库属性优先级，容器与普通 CLI 仍使用环境变量；修复后数据库恢复场景和完整联合清单通过。
- 最终证据：在干净提交 `878f7efbc3bc9e82a5e2b73903ec6d25ba425280` 上运行 `./platform drill-stage-2`，12 个场景 `12/12` 通过，前置与恢复后各 13 项诊断通过，总耗时 `15.884760s`，本地证据记录 `repository_dirty=false`。该结果只证明本机小规模可靠性正确性，不替代条件容量认证。
- 自动验收：P2-11 清单、Schema、执行、失败关闭和证据专项 `7/7`；统一 `./scripts/verify` 通过 React `42/42`、Python `543/543`、Ruff format/lint `497` 个文件、mypy strict `497` 个源文件、模块依赖、中文注释、UnoCSS、OpenAPI/生成契约兼容、Secret Scanner、SBOM、ReleaseManifest 和生产构建。

### P2-12 阶段验收与关闭

- 状态：已完成；发布兼容基线提交 `7dfa517`，阶段关闭提交在进度看板回填，关闭标签为 `stage-2-complete`。
- ReleaseManifest：新增 [`阶段 2 本地可靠性发布报告`](../releases/stage-2-local-reliability/README.md)，生成 `0.2.0` V1 清单，固定提交 `7dfa517` 的 Web、API、Worker 与契约源码摘要、数据库 Revision `20260815_0041`、Node.js `24.19.0`、Python `3.12.12` 和 7 个 `linux/arm64` 本地验收镜像内容摘要。清单自摘要为 `sha256:17ae80eef51038152a502683c054c441a10346a45ed4db2105084ca4707b245d`，生成漂移和 `p1a-02-v1` 兼容矩阵检查通过。
- 自动门禁：最终 `./scripts/verify` 通过 React `42/42`、Python `544/544`、Ruff format/lint `497` 个文件、mypy strict `497` 个源文件，以及前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、供应链和生产构建。第一次受限沙箱运行只有本地端口访问被拒绝，不依赖本地服务的 `427` 项测试通过；允许访问同一组本地依赖后原样重跑，最终全量通过。
- 运行与可靠性：`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision `20260815_0041`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过。再次执行 `./platform drill-stage-2`，固定场景 `12/12` 通过，执行前后各 13 项诊断通过；P2-11 在干净实现提交上的 `repository_dirty=false` 证据继续作为清单实现基线，本次重跑覆盖尚未提交的 P2-12 发布文档。
- 浏览器门禁：合成个人空间 Owner 在 `1440×900` 和 `390×844` 验收运行状态页及任务、索引、Outbox、审计与用量、生命周期五个运营页签。两个视口均只有一个 `h1`，文档宽度分别稳定为 1440px 和 390px，控制台无错误；移动端只允许页签和 328px 宽表格在局部容器滚动，导航抽屉及全部运营入口可访问。
- 供应链边界：开发级门禁为 `passed`；正式发布级门禁按设计返回 `blocked`，原因仍为镜像漏洞扫描 `not_configured` 和 Linux 宿主机验收 `not_run`。因此不创建正式发布供应链归档，不把本地镜像内容 ID 冒充注册表多架构摘要。
- 范围边界：本节点只关闭阶段 2，不提前实现阶段 3 Agent 控制面、阶段 4 工具副作用或阶段 5 质量与私有化能力，也不扩展 SaaS、Go、真实多源连接器、LLM Grading、多模态图片问答、Channel Gateway 或 Durable Run。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、质量、成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机和生产容量结论。
- 镜像扫描为 `not_configured`，正式发布门禁保持阻断。
- 阶段 2 已按冻结范围关闭；阶段 3～5 和项目后置能力继续按独立计划建设。

## 5. 阶段结论

`passed`。`P2-01`～`P2-12` 全部完成，阶段可靠性结论为 `passed`；任务恢复、Worker 隔离、索引重建、跨实例 SSE、可观测性、审计与 Outbox 运营、数据生命周期、权限传播、运营工作台和联合故障演练均形成自动化、容器、浏览器或恢复证据。最终统一门禁 React `42/42`、Python `544/544`，联合演练 `12/12`，Revision `20260815_0041` 的 13 项容器诊断通过，`0.2.0` 本地可靠性版本以 `stage-2-complete` 标签冻结。

该结论不改变独立状态：真实供应商和 AI 质量为 `not_configured`，容量与 Linux 为 `not_run`，镜像扫描为 `not_configured`，正式发布门禁继续 `blocked`。下一正式建设阶段为阶段 3 Agent 控制面、发布、灰度与回滚。
