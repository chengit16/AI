# 阶段 2 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 2：可靠性、数据治理与运营增强 |
| 状态 | 进行中 |
| 报告日期 | 2026-08-15 |
| 当前节点 | `P2-04` 索引巡检与重建进行中 |
| 阶段可靠性 | `not_run` |
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
| 数据库基线 | PostgreSQL 16，当前开发 Revision `20260815_0037`；阶段 1 发布仍冻结在 `20260815_0035` |
| 阶段 1 发布 | 本地 MVP `0.1.0`，ReleaseManifest 摘要 `e8983e87…b62943` |
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

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、质量、成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机和生产容量结论。
- 镜像扫描为 `not_configured`，正式发布门禁保持阻断。
- 阶段 2 只处理可靠性、数据治理和运营，不扩展阶段 3～5 或项目后置能力。

## 5. 阶段结论

`not_run`。`P2-01`～`P2-03` 已完成可靠性契约、任务恢复事实、Worker 隔离和索引死信恢复，但阶段可靠性必须等待后续节点及 `P2-11` 联合演练；当前进入 `P2-04`。
