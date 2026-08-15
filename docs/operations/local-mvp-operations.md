# 本地 MVP 运维与故障处理手册

## 1. 适用范围

本文档适用于 AI 智能平台阶段 1 本地 MVP，覆盖个人空间和企业空间的一键启动、日常诊断、日志查看、平台管理员管理、模型供应商接入、备份恢复、导入导出、主密钥轮换、升级回退和常见故障处理。

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
                    └── Worker         入库、索引和异步任务
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
./platform logs worker
./platform restart
./platform stop
```

`stop` 只停止容器并保留宿主机数据。`restart` 会重新构建当前源码对应镜像并执行 Migration，运行前应确认当前代码版本与数据版本兼容。

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

`./platform doctor` 必须全部通过以下八项：Web、API、MinIO、Tika、PostgreSQL、数据库 Revision、Valkey 和 Worker。建议先执行：

```bash
./platform status
./platform doctor
./platform logs api
./platform logs worker
```

日志排查原则：

- 使用 Request ID、Trace ID、Run ID、任务 ID 或资源 ID 关联请求，不搜索用户正文和凭据。
- Readiness 返回 `503` 时先查看结构化依赖状态；依赖降级不等于 API 进程不可达。
- Worker 异常先检查 PostgreSQL、Valkey、MinIO 和 Tika，再检查任务重试、租约和死信事实。
- 不把 Cookie、`Authorization`、模型 Key、字段级 ABAC 受限内容或完整文档正文粘贴到工单和文档。

## 8. 备份、导出、恢复与导入

### 8.1 创建恢复包

```bash
./platform backup
./platform backup AIPlatformBackups/manual-20260815.aiprb
./platform export AIPlatformBackups/export-20260815.aiprb
```

`backup` 与 `export` 使用同一 V1 `.aiprb` 格式。平台先校验对象引用，再停止 API、Worker 和 MinIO 取得同一停写窗口的 PostgreSQL、对象和文件密钥快照，最后使用独立恢复密钥执行流式 AES-256-GCM 加密。输出文件不能已存在，也不能位于 `AI_PLATFORM_ROOT` 内。

备份完成后必须记录文件路径、创建时间、Schema Revision 和恢复密钥保管位置。恢复包与 `.ai-platform/secrets/backup-encryption.key` 丢失任意一项都无法跨机器恢复。

### 8.2 恢复或导入

恢复会替换当前本地事实，必须先确认目标实例和恢复包：

```bash
./platform restore AIPlatformBackups/manual-20260815.aiprb --confirm-replace
./platform import AIPlatformBackups/export-20260815.aiprb --confirm-replace
```

平台会先自动生成 `pre-restore-*.aiprb` 回退点，再验证恢复包认证标签、路径、校验和和文件集合；随后停止写入方、恢复数据库/对象/密钥、清空可重建 Valkey 状态、执行 Migration、检查对象引用和八项诊断。任一步失败时不得手工标记成功，应保持服务停止并使用操作前恢复点处理。

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
| Worker 不健康 | Worker、Valkey、PostgreSQL 日志 | 恢复依赖后观察有限重试和租约；不要直接改任务事实终态 |
| 文档解析失败 | Tika 状态、任务失败码、文件类型/大小 | 修复依赖或输入后使用受控人工重试，保留失败事实 |
| 无法访问页面或接口 | 成员状态、角色、菜单发布、PDP 决策 | 按最小权限修正授权；不要把前端菜单显示当成授权证据 |
| SSE 重连异常 | Run 状态、`Last-Event-ID`、事件保留期 | 在 24 小时保留期内按游标重连；超预算或过期时使用消息快照 |
| 模型探测失败 | HTTPS 域名白名单、DNS、数据政策、模型标识 | 修正平台配置；禁止关闭 SSRF 和数据政策门禁 |
| 恢复包无法解密 | 恢复包路径、独立恢复密钥、文件权限 | 使用与该包配对的密钥；无法配对时不能强行恢复 |

## 12. 日常检查清单

- 每次代码或配置升级前创建加密恢复包。
- 每次启动、恢复、密钥轮换和升级后执行 `./platform doctor`。
- 定期验证恢复包可在隔离环境解密、迁移和完成对象引用检查。
- 临时平台管理员完成操作后立即撤权。
- 只使用合成数据做本地测试；真实敏感资料不得直接作为测试样本。
- 真实供应商调用前确认域名、凭据、能力和数据政策均已审核。
- 记录未执行的 Linux、容量、镜像扫描和真实 AI 质量状态，不用本地 Mock 结果替代。

