# 本地 MVP 运维与故障处理手册

## 1. 适用范围

本文档适用于 AI 智能平台本地 MVP 与阶段 2 可靠性增强，覆盖个人空间和企业空间的一键启动、日常诊断、日志与指标查看、平台管理员管理、模型供应商接入、备份恢复、导入导出、主密钥轮换、升级回退和常见故障处理。

当前完成态是浏览器访问的 Web 系统，不是原生桌面应用。默认只绑定 `127.0.0.1`，不应通过修改端口绑定直接暴露到局域网或公网。macOS + Docker Desktop 已完成实际验收；Linux 是目标运行平台，但宿主机验收仍为 `not_run`；Windows 尚未进入首期支持范围。

本手册只证明本地小规模 MVP 可运行，不代表生产容量、正式商业发布、真实模型质量或 SaaS 部署已经通过。

## 2. 运行组成与数据边界

```text
浏览器
  └── Web / Nginx :3000
        └── FastAPI :8000
              ├── PostgreSQL :5432    业务、权限、审计、任务和 SSE 事实
              ├── Valkey :6379        Session、缓存、Broker 和可重建状态
              ├── MinIO :9000/:9001   上传原件与派生对象
              └── Tika :9998          文档解析与 OCR

Scheduler                           周期唤醒，不执行业务任务
  ├── Control Worker               Outbox 与内部集成事件
  ├── Parsing Worker               TXT、Markdown、DOCX 解析
  ├── OCR Worker                   PDF 与图片 OCR
  ├── Embedding Worker             不可见 Chunk 与向量生成
  └── Indexing Worker              索引提交与原子发布
```

默认目录由 `.env` 控制：

| 配置 | 默认路径 | 内容 | 备份规则 |
| --- | --- | --- | --- |
| `AI_PLATFORM_ROOT` | `./.ai-platform` | PostgreSQL、Valkey、MinIO、日志、运行时文件和本地密钥 | 不得提交 Git；恢复包不能写入该目录 |
| `AI_PLATFORM_BACKUP_ROOT` | `./AIPlatformBackups` | `.aiprb` 加密恢复包 | 与恢复包加密密钥分开保管 |
| `master.key` | `.ai-platform/secrets/master.key` | 模型供应商凭据的数据密钥包裹主密钥 | 进入加密恢复包 |
| `task-signing.key` | `.ai-platform/secrets/task-signing.key` | 内部任务信封签名密钥 | 进入加密恢复包 |
| `backup-encryption.key` | `.ai-platform/secrets/backup-encryption.key` | `.aiprb` 恢复包解密密钥 | 不进入恢复包，必须另行保管 |

Valkey、日志和 `runtime` 不作为业务事实备份；恢复后由平台重建。删除 `.ai-platform` 会永久删除本地业务数据和密钥，不能把重新启动视为恢复手段。

## 3. 安装前检查

### 3.1 必需软件

- Docker Engine 与 Docker Compose v2；macOS 使用 Docker Desktop。
- `curl` 与 `openssl`。
- 修改或验收源码时使用 Node.js 24、pnpm 11、Python 3.12 和 uv；只运行已构建容器时不要求宿主机直接执行应用运行时。

### 3.2 主机资源

启动器建议至少 8 核 CPU、16 GB 内存和 50 GB 可用磁盘。CPU 或内存不足只给出警告并限制处理速度、并行任务和本地数据规模；可用磁盘低于 `AI_PLATFORM_MIN_FREE_DISK_GB` 时启动会失败关闭。

阶段 1 功能验收曾使用 `AI_PLATFORM_MIN_FREE_DISK_GB=40` 适配当前开发机，但 `.env.example` 的 50 GB 默认安全基线没有降低。该覆盖不构成容量认证。

### 3.3 端口

默认需要本机端口 `3000`、`8000`、`5432`、`6379`、`9000`、`9001` 和 `9998`。启动器发现端口被非本项目进程占用时会停止；确需调整时在 `.env` 中修改对应端口，并保持只绑定回环地址。

## 4. 首次启动与停止

```bash
./platform start
./platform doctor
```

首次启动会从 `.env.example` 创建本地 `.env`，建立数据目录，并以 `0600` 权限生成平台主密钥和任务签名密钥。服务全部健康后访问：

- 正式应用：`http://127.0.0.1:3000/`
- 运行状态：`http://127.0.0.1:3000/status`

日常命令：

```bash
./platform status
./platform logs
./platform logs api
./platform logs worker-control
./platform logs worker-parsing
./platform logs worker-ocr
./platform logs worker-embedding
./platform logs worker-indexing
./platform logs scheduler
./platform restart
./platform stop
```

`stop` 只停止容器并保留宿主机数据。`restart` 会重新构建当前源码对应镜像并执行 Migration，运行前应确认当前代码版本与数据版本兼容。

启动、重建、恢复和诊断必须使用 `./platform`。不要用裸 `docker compose up` 代替统一入口，因为 `.env` 中相对数据目录需要先按项目根目录解析为绝对路径；绕过该步骤可能把空目录挂载为密钥文件或数据目录。

## 5. 账号、空间与平台管理员

普通用户通过 Web 注册；每个账号只创建一个默认个人空间。企业空间、邀请、成员、部门、角色、菜单和权限均在业务界面管理。

平台管理员独立于个人或企业空间，只能通过本机命令授予或撤销：

```bash
./platform admin grant user@example.com
./platform admin revoke user@example.com
```

完成模型治理等临时操作后应立即撤权。菜单隐藏只改善界面体验，接口、数据和字段权限始终由后端策略复核；遇到 `POLICY_DENIED` 时应核对空间成员状态、角色、资源范围和字段策略，不能通过修改前端绕过。

## 6. 模型供应商接入

本地首次问答默认使用只允许 `local/test` 的内置 Mock Provider，用于验证功能链路，不会访问真实模型，也不能代表回答质量和成本。

接入 GPT 中转或国内 OpenAI-compatible 供应商前：

1. 在 `.env` 的 `MODEL_PROVIDER_ALLOWED_HOSTS` 中显式加入公网 HTTPS 域名，例如 `MODEL_PROVIDER_ALLOWED_HOSTS=["relay.example.com"]`。
2. 执行 `./platform restart` 让网络白名单生效。
3. 由平台管理员进入模型治理页，登记 `base_url`、模型标识、凭据、能力和数据政策。
4. 先使用合成数据执行能力探测；数据政策未审核时禁止发送真实或敏感数据。
5. 发布不可变运行配置后再切换业务调用；失败时回滚到上一运行配置版本。

平台拒绝 HTTP、内网地址、非 443 端口、混合 DNS、重定向和未登记域名。供应商 Key 只能通过服务端页面录入，不得写入 `.env`、源码、日志或 Git。

## 7. 健康检查与日志定位

`./platform doctor` 必须通过 13 项实际检查：Web、API、MinIO、Tika、PostgreSQL、数据库 Revision、Valkey、五个 Worker Lane 和 Scheduler。Worker 健康同时要求 32 字节任务签名密钥可读和 Celery 节点可响应；Scheduler 不挂载文件型密钥，健康检查要求 Beat 进程仍在运行。

建议先执行：

```bash
./platform status
./platform doctor
./platform logs api
./platform logs worker-control
./platform logs worker-ocr
./platform logs scheduler
```

日志排查原则：

- 使用 Request ID、Trace ID、Run ID、任务 ID 或资源 ID 关联请求，不搜索用户正文和凭据。
- Readiness 返回 `503` 时先查看结构化依赖状态；依赖降级不等于 API 进程不可达。
- 单个 Worker Lane 异常时先确认其他 Lane 是否仍健康，再检查 PostgreSQL、Valkey、MinIO、Tika、对应队列积压、租约和死信事实。
- Scheduler 异常不会立即终止正在执行的任务，但新一轮扫描不会被唤醒；恢复后由 PostgreSQL 租约和幂等规则继续处理，不能手工复制任务绕过恢复机制。
- 索引巡检默认每 300 秒进入 `platform.indexing` 队列，间隔可通过 `INDEX_INSPECTION_INTERVAL_SECONDS` 在 `60～86400` 秒内调整；异常时先检查 Scheduler、Indexing Worker 和维护运行事实。
- 不把 Cookie、`Authorization`、模型 Key、字段级 ABAC 受限内容或完整文档正文粘贴到工单和文档。

### 7.1 可观测性

运行状态页展示每项依赖的延迟、最近检查时间、关键性和稳定降级原因。Prometheus 抓取入口为 `http://127.0.0.1:8000/api/v1/metrics`，聚合 API 与五个 Worker 的多进程指标；共享指标文件位于 `.ai-platform/runtime/prometheus`，属于可删除运行数据，不进入业务备份。

容器日志使用固定字段单行 JSON。平台事件可以按 Trace ID、Request ID 和 Task ID 关联；Uvicorn、Celery 和第三方库的原始消息会被安全 Formatter 丢弃，避免 URL 参数、任务载荷或异常正文旁路字段注册表。可选 OTLP Trace、指标明细、告警规则和排障顺序见 [可观测性与告警运维手册](./observability.md)。

### 7.2 索引巡检与重建

索引巡检只读取 PostgreSQL 中的文档发布、成功入库、索引版本、发布指针和 Chunk 引用，不把正文或向量复制到巡检证据。`index_maintenance_runs` 保存扫描、差异、修复、排队和清理计数，`index_inspection_findings` 保存稳定差异码及处理状态。

自动恢复遵循以下顺序：

1. 当前文档版本存在完整的 `ready/active/retired` 候选时，先锁定当前发布事实，再原子切换索引版本、Chunk 活动状态和发布指针。
2. 没有完整候选时，先撤销异常活动面，避免残缺或错误引用继续召回，再从成功入库事实幂等排队新构建。
3. 全量重建不会覆盖健康旧索引；新构建的 Chunk 在完整提交前保持 `active=false`，构建失败也不会对检索可见。
4. 清理只处理永久失败或三次人工恢复耗尽的不可见 Chunk，仍可恢复的死信和活动索引不得删除。

当前周期巡检由 Scheduler 自动执行。人工巡检、全量重建和失败产物清理已在“运行状态 → 运营工作台 → 索引”接入权限、命令专属固定确认词、结构化原因码、幂等、审计和 Outbox；请求由 Indexing Worker 租约认领，失败最多重试 3 次后进入 `dead_letter`。不得直接修改索引表或伪造维护运行记录。出现持续 `unresolved` 时保留运行 ID、差异码、工作空间和文档 ID，检查成功入库 Artifact 是否仍存在，再通过受控入口处理，禁止记录文档正文。

### 7.3 审计、用量与 Outbox 运营

P2-07 提供工作空间级运营 API，P2-10 已将任务、索引、Outbox、审计与用量、生命周期汇总到“运行状态 → 运营工作台”。个人空间和企业空间使用同一安全边界：只有默认 `workspace_owner` 获得 `operations.records.read` 和 `operations.outbox.replay`，新增入库取消与索引维护动作也分别校验独立权限；自定义菜单或页面可见性不能替代服务端权限。全部读取必须绑定可信工作空间，其他空间的游标也会被拒绝。

| 能力 | API | 运营边界 |
| --- | --- | --- |
| 审计查询 | `GET /api/v1/workspaces/{workspace_id}/operations/audit-records` | 可按主体、动作、资源类型、结果和时间筛选；返回权限码、策略决策 ID、策略版本与 Trace，不返回自由属性 |
| 用量明细 | `GET /api/v1/workspaces/{workspace_id}/operations/usage-records` | 可按计量项和周期筛选不可变账本，游标分页上限为 200 条 |
| 用量对账 | `GET /api/v1/workspaces/{workspace_id}/operations/usage-reconciliation` | 对比当前计数器、明细累计和最后结果；发现差异只报告，不自动改账 |
| Outbox 查询 | `GET /api/v1/workspaces/{workspace_id}/operations/outbox-events` | 可按状态、事件类型和时间筛选；响应刻意不包含事件 Payload |
| 集成巡检 | `GET /api/v1/workspaces/{workspace_id}/operations/integration-inspection` | 汇总状态、最老积压、过期租约、不兼容 Schema、重放请求、重复投递和幂等异常 |
| Outbox 重放 | `POST /api/v1/workspaces/{workspace_id}/operations/outbox-events/{event_id}/replay` | 只接受浏览器主体、客户端幂等键和结构化原因码，只能重放 `published` 或 `dead_letter` 事件 |

日常巡检先看 `oldest_pending_age_seconds`、`expired_claim_count`、`incompatible_schema_count` 和 `idempotency_issue_count`。最老积压超过 300 秒、存在不兼容 Schema 或幂等异常时，应保留工作空间、事件 ID、稳定错误码、Request ID 和 Trace ID，检查 Outbox 发布器与对应消费者；禁止把 Payload、用户正文或凭据复制到排障记录。

人工重放必须满足以下规则：

1. 先确认来源事件已是 `published` 或 `dead_letter`，并已排除消费者实现、Schema 或依赖仍然故障的情况。
2. 使用 8～128 字符的稳定 `idempotency_key`，同一次运营动作重试必须复用原值；`reason_code` 使用 3～64 字符的大写结构化原因码。
3. 重放保留原 `event_id`，重放请求事实、事件重新排队和审计记录在同一事务提交。重复请求返回同一不可变重放记录，不能生成第二次运营动作。
4. 消费者收到相同事件时只更新 `delivery_count` 和 `last_received_at`，不得再次执行已完成的业务投影。重放后应再次检查集成巡检、事件状态和消费回执。

用量对账的 `consistent=false` 代表计数器、账本累计或最后结果存在差异。当前接口不会自动修复，也不得直接修改 `usage_counters` 或用量明细；应保存计量项、周期、计数器版本和差异值，等待后续受控修复能力。审计、用量明细、消费回执和重放请求均属于追溯事实，不通过普通运营接口删除或覆盖。

### 7.4 阶段 2 联合故障演练

平台运行且 `./platform doctor` 正常时，可执行全合成联合演练：

```bash
./platform drill-stage-2
```

命令固定读取 `tests/fixtures/reliability/p2-11-v1.json`，覆盖 PostgreSQL 逻辑恢复、MinIO 引用、对象快照、索引重建、Worker 租约、API 实例切换、SSE 通知丢失、Valkey 不可用、Outbox 积压与重复投递，以及恢复后身份边界和撤权失败关闭。清单只调用仓库内已登记的 12 个 pytest 节点，不访问真实模型供应商或真实客户资料。

执行器先运行 13 项前置诊断，再一次性执行场景并解析 JUnit，最后无条件运行恢复后诊断。前置诊断失败时场景保持 `not_run`；任一场景为 `failed/error/skipped/not_run`、pytest 非零退出或恢复后诊断失败时，命令整体非零退出。不得手工修改证据状态或删掉失败样本后重新统计。

最新结果原子写入 `.ai-platform/evidence/p2-11-latest.json`。该忽略目录只保存本机证据，不进入 Git 或业务备份；JSON 记录清单摘要、Git Revision、工作区是否干净、起止时间、耗时、检查结果和稳定原因码，不保存测试 stdout、stderr、异常正文、业务载荷或凭据。失败时先根据控制台中的测试节点和稳定原因码修复根因，确认平台诊断正常后完整重跑，不能只单独改写失败项。该演练验证小规模正确性，不代表条件容量认证。

### 7.5 阶段 3 联合验收

平台运行且 `./platform doctor` 正常时，可执行阶段 3 全合成联合验收：

```bash
./platform accept-stage-3
```

命令固定读取 `tests/fixtures/agent-control/p3-13-v1.json`，调用 15 个已登记 pytest 节点，完整覆盖阶段 3 的 10 条冻结不变量。验收包含个人/企业审批、失败测试与跨空间发布阻断、Release 不可变、写工具拒绝、草稿执行拒绝、控制面故障、Run 精确绑定、在途完成、灰度回滚、并发晋级、三类服务出口、控制台全链路和运营晋级门禁，不访问真实供应商或客户资料。

执行器先运行 13 项前置诊断，再一次性执行场景并解析 JUnit，最后无条件运行恢复诊断。前置诊断、pytest、任一场景或恢复诊断失败都会使整次验收非零退出。最新证据原子写入 `.ai-platform/evidence/p3-13-latest.json`，只记录清单摘要、Git Revision、工作树状态、耗时、稳定原因码和场景状态；阶段关闭证据必须来自干净提交且 `repository_dirty=false`。

阶段关闭或本地版本切换后还应复验目标 `ReleaseManifest` 和供应链状态：

```bash
.venv/bin/python scripts/generate_release_manifest.py \
  --inputs docs/releases/stage-3-local-agent-platform/release-manifest-input.v1.json \
  --output docs/releases/stage-3-local-agent-platform/release-manifest.v1.json \
  --check
.venv/bin/python -m scripts.check_release_readiness --require development --check
.venv/bin/python -m scripts.check_release_readiness --require release --check
```

前两项必须通过。当前正式发布检查必须因镜像扫描 `not_configured` 和 Linux 验收 `not_run` 返回非零；只有补齐可信外部证据后才允许转为 `passed`，不得为了生成发布包而手工修改状态清单。

### 7.6 阶段 4 联合故障演练与验收

平台运行且 `./platform doctor` 的 13 项诊断正常时，先执行阶段 4 工具故障演练，再执行阶段联合验收：

```bash
./platform accept-stage-4-tools
./platform accept-stage-4
```

前一入口固定运行 `p4-12-v1` 八场景、九个 case，覆盖重复投递、Adapter 超时、响应丢失、凭证撤销、审批过期、取消竞态、Worker 重启和跨空间拒绝；后一入口固定运行 `p4-13-v1` 十五场景，复核 12 条阶段不变量和六项关闭门禁。两个执行器均在场景前后运行 13 项诊断，任一诊断、场景、跳过、清单漂移或证据异常都会失败关闭。

最新最小证据分别原子写入 `.ai-platform/evidence/p4-12-latest.json` 与 `.ai-platform/evidence/p4-13-latest.json`。阶段关闭证据必须绑定干净 Git 修订并满足 `repository_dirty=false`；本地证据目录保持忽略，不得将测试输出、业务正文、参数正文或凭证明文写入证据或 Git。

阶段关闭或本地版本切换后复验 `0.4.0` ReleaseManifest：

```bash
.venv/bin/python scripts/generate_release_manifest.py \
  --inputs docs/releases/stage-4-local-tool-execution/release-manifest-input.v1.json \
  --output docs/releases/stage-4-local-tool-execution/release-manifest.v1.json \
  --check
```

该检查只证明清单与冻结输入一致。开发级供应链必须通过；镜像扫描或 Linux 宿主机验收未配置时，正式发布继续保持 `blocked`，不能用本地联合验收替代生产发布证据。

## 8. 备份、导出、恢复与导入

### 8.1 创建恢复包

```bash
./platform backup
./platform backup AIPlatformBackups/manual-20260815.aiprb
./platform export AIPlatformBackups/export-20260815.aiprb
```

`backup` 与 `export` 使用同一 V1 `.aiprb` 格式。平台先校验对象引用，再停止 API、五个 Worker、Scheduler 和 MinIO，取得同一停写窗口的 PostgreSQL、对象和文件密钥快照，最后使用独立恢复密钥执行流式 AES-256-GCM 加密。输出文件不能已存在，也不能位于 `AI_PLATFORM_ROOT` 内。

备份完成后必须记录文件路径、创建时间、Schema Revision 和恢复密钥保管位置。恢复包与 `.ai-platform/secrets/backup-encryption.key` 丢失任意一项都无法跨机器恢复。

### 8.2 恢复或导入

恢复会替换当前本地事实，必须先确认目标实例和恢复包：

```bash
./platform restore AIPlatformBackups/manual-20260815.aiprb --confirm-replace
./platform import AIPlatformBackups/export-20260815.aiprb --confirm-replace
```

平台会先自动生成 `pre-restore-*.aiprb` 回退点，再验证恢复包认证标签、路径、校验和和文件集合；随后停止写入方、恢复数据库/对象/密钥、清空可重建 Valkey 状态、执行 Migration、检查对象引用和 13 项诊断。任一步失败时不得手工标记成功，应保持服务停止并使用操作前恢复点处理。

不要在公共或唯一实例上做无演练计划的恢复测试。优先使用独立目标目录和隔离数据库完成恢复演练。

## 9. 主密钥轮换

```bash
./platform rotate-master-key --confirm
```

轮换前平台自动生成加密恢复包，随后停止 API，在单一数据库事务中重包裹供应商凭据数据密钥，再提升文件密钥版本并执行健康诊断。命令成功前不能删除旧密钥或轮换前恢复包。

建议在新增或撤换运维人员、怀疑密钥泄漏、恢复到其他机器或按内部周期要求时轮换。恢复包加密密钥不随主密钥轮换自动变化，需要独立的保管和轮换方案。

## 10. 升级与回退

### 10.1 升级

1. 确认目标提交或标签附带兼容的 `ReleaseManifest`，数据库 Revision 位于兼容矩阵内。
2. 执行 `./scripts/verify` 和 `./platform doctor`，记录升级前状态。
3. 执行 `./platform backup`，把恢复包和恢复密钥分开保存。
4. 切换到目标代码版本后执行 `./platform restart`。
5. 再次执行 `./platform doctor`，检查登录、空间切换、知识问答、SSE 恢复和工作流审批关键路径。

### 10.2 失败回退

生产式回退以“恢复升级前加密备份 + 回到兼容应用版本”为准，不依赖 Down Migration 修补已经写入的新数据：

1. 保持业务写入停止。
2. 切回升级前 Git 标签或制品。
3. 使用升级前 `.aiprb` 执行显式恢复。
4. 执行 `./platform doctor` 和关键业务冒烟。
5. 保存失败日志、Revision、Request ID 和 ReleaseManifest 摘要，禁止记录凭据或用户正文。

## 11. 常见故障处理

| 现象 | 优先检查 | 处理建议 |
| --- | --- | --- |
| Docker 不可用 | `docker info`、Docker Desktop 状态 | 启动 Docker Engine；不要绕过运行时检查 |
| 启动提示端口占用 | `lsof -nP -iTCP:<port> -sTCP:LISTEN` | 停止冲突进程或修改 `.env` 的本地端口 |
| 可用磁盘不足 | `df -h`、`AI_PLATFORM_MIN_FREE_DISK_GB` | 清理非平台文件或扩容；不得用过低阈值冒充正式基线 |
| Web 正常但 API 为 `503` | `/api/v1/health/ready`、API 日志 | 根据结构化结果定位 PostgreSQL、Valkey、MinIO 或 Tika |
| 数据库 Revision 失败 | `./platform doctor`、migrate 日志 | 停止写入，确认代码/Manifest/Schema 组合；失败升级按备份回退 |
| 单个 Worker Lane 不健康 | 对应 Lane 日志、签名密钥挂载、Valkey、PostgreSQL | 保持其他 Lane 运行；通过 `./platform restart` 恢复后观察积压、有限重试和租约，不直接改任务事实终态 |
| Scheduler 不健康 | Scheduler 日志、签名密钥挂载、Valkey | 通过统一入口恢复 Scheduler；依赖 PostgreSQL 扫描补偿，不手工跨队列投递 |
| 某条队列持续积压 | 对应 Worker Lane、并发参数、任务阶段和死信 | 先排除依赖故障，再在 `1～8` 范围内单独调整该 Lane 并发；不得让其他 Worker 消费该队列 |
| 索引巡检持续发现差异 | 最新维护运行、稳定差异码、Indexing Worker、成功入库 Artifact | 保留异常活动面已撤销的安全状态；等待幂等重建，存在完整候选时由巡检自动恢复；不得直接改 Chunk 或发布指针 |
| 文档解析失败 | Tika 状态、任务失败码、文件类型/大小 | 修复依赖或输入后使用受控人工重试，保留失败事实 |
| 无法访问页面或接口 | 成员状态、角色、菜单发布、PDP 决策 | 按最小权限修正授权；不要把前端菜单显示当成授权证据 |
| SSE 重连异常 | Run 状态、`Last-Event-ID`、事件保留期 | 在 24 小时保留期内按游标重连；超预算或过期时使用消息快照 |
| 模型探测失败 | HTTPS 域名白名单、DNS、数据政策、模型标识 | 修正平台配置；禁止关闭 SSRF 和数据政策门禁 |
| 恢复包无法解密 | 恢复包路径、独立恢复密钥、文件权限 | 使用与该包配对的密钥；无法配对时不能强行恢复 |
| 联合故障演练失败 | `.ai-platform/evidence/p2-11-latest.json`、控制台失败节点、前后诊断 | 保留失败证据并修复根因，再完整执行 `./platform drill-stage-2`；不得跳过失败场景或手工改状态 |

## 12. 日常检查清单

- 每次代码或配置升级前创建加密恢复包。
- 每次启动、恢复、密钥轮换和升级后执行 `./platform doctor`。
- 阶段关闭或可靠性能力变更后执行 `./platform drill-stage-2`，并确认 Git Revision 与工作区状态可追溯。
- 定期验证恢复包可在隔离环境解密、迁移和完成对象引用检查。
- 临时平台管理员完成操作后立即撤权。
- 只使用合成数据做本地测试；真实敏感资料不得直接作为测试样本。
- 真实供应商调用前确认域名、凭据、能力和数据政策均已审核。
- 记录未执行的 Linux、容量、镜像扫描和真实 AI 质量状态，不用本地 Mock 结果替代。
