# 阶段 3 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 3：Agent 控制面与服务发布 |
| 状态 | 进行中 |
| 报告日期 | 2026-08-16 |
| 当前节点 | `P3-07` 服务与路由治理 |
| 阶段 1 `core_functional` | `passed`，继承标签 `stage-1-complete` |
| 阶段 2 可靠性 | `passed`，继承标签 `stage-2-complete` |
| Agent 控制面契约基线 | `passed` |
| Agent 控制面 | `in_progress`，生命周期、配置校验、自动评估、发布审批与不可变 Release 已通过 |
| 服务发布与回滚 | `not_run` |
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
| 数据库基线 | PostgreSQL 16，Revision `20260816_0044` |
| 阶段 2 发布 | 本地可靠性版本 `0.2.0`，ReleaseManifest 摘要 `17ae80ee…7b245d` |
| 数据与模型 | 只使用版本化合成数据；默认 Mock Provider，不代表真实供应商或 AI 质量 |

## 3. 节点记录

按 [`阶段 3 实施计划`](./stage-3-plan.md) 持续追加每个节点的完成日期、实现边界、测试计数、故障演练、容器/浏览器结果、已知限制和 Git SHA。未实际执行的检查保持 `not_run` 或 `not_configured`。

### P3-01 Agent 控制面契约与安全基线

- 状态：已完成，提交 `88a2a2f`。
- 核心事实契约：新增 `agent-control.v1` Draft 2020-12 Schema 和全合成 Golden Fixture，固定工作空间隔离的 `Agent`、可变 `AgentDraft`、绑定测试与审批的发布候选、不可变 `AgentRelease`、`Service`、版本化 `ServiceRoute` 和 Run 唯一发布绑定。发布快照必须包含 Prompt、模型运行配置、知识范围、工作流、只读工具、输出、安全和预算版本，以及固定测试集和审批证据。
- 状态机与不变量：`p3-01-v1` 冻结 Agent、AgentDraft 和 Service 三个状态机及 10 条安全不变量；`AgentRelease` 不设置可变状态机，任何修改或删除都必须失败。Runtime 只接受 Release、Run 绑定不漂移、控制面故障隔离、发布失败关闭、路由只追加、工作空间统一授权、只读工具和确定性离线评估均有明确执行层和证据源。
- 个人与企业治理：个人空间由 `workspace_owner` 完成至少一级审批并允许所有者自批；企业空间复用既有审批策略和多级审批引擎，自审规则继续由策略定义。两类空间共同预留 19 个权限和 15 个菜单标识，所有菜单同时声明个人与企业适用。
- 接口与事件边界：冻结 19 个计划 OpenAPI Operation 和 8 类集成事件，新增 8 个稳定错误码。计划标识不会冒充已上线能力：专项测试确认这些权限和 Operation 尚未进入阶段 2 的活动资源注册表，后续节点只有在路由、策略、菜单、Migration 和测试同时完成后才逐步激活。
- 合成验收集：新增 17 个全合成场景，覆盖个人所有者发布、企业多级审批、草稿执行拒绝、功能/安全测试失败、审批缺失和摘要失效、跨空间发布、Release 修改、控制面故障、稳定灰度、回滚、并发晋级、隐藏菜单直调 API、写工具拒绝、Run 改绑和 Open API Key Scope 收窄；全部场景固定零重复副作用和零跨空间暴露。
- 自动验收：P3-01 与共享契约专项 `38/38`；统一 `./scripts/verify` 通过 React `42/42`、Python `558/558`、Ruff format/lint `498` 个文件、mypy strict `498` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。相对 `stage-2-complete` 及 `HEAD` 的同主版本兼容检查均通过。
- 容器验收：`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision `20260815_0041`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过。本节点未修改数据库、Runtime 或页面，因此不重复执行 Migration、浏览器和阶段 2 联合故障演练。
- 当前边界：本节点只建立后续实现必须共同遵守的契约和测试事实，不提前实现 Agent 数据表、发布审批、服务路由、控制台页面或后置能力。
- 提交：`88a2a2f`。

### P3-02 Agent 生命周期与不可变发布事实

- 状态：已完成，提交 `6339144`。
- 生命周期事实：在共享 `agents` 和 `agent_releases` 上区分 `system/custom` 写入边界，新增唯一当前 `AgentDraft`、只追加 `AgentDraftRevision`、绑定草稿 revision 的 `AgentReleaseCandidate` 和不可变 `AgentControlRequest`。系统知识助手仍由原 `assistant` 模块管理，自定义控制面读取系统或跨空间 Agent 时统一失败关闭。
- 应用行为：支持自定义 Agent 创建、草稿乐观锁更新、历史 revision 查询、候选来源冻结、Agent 归档和自定义 Release 隔离读取；所有写操作使用稳定请求摘要和幂等键。并发创建可在唯一键竞争回滚后恢复同一提交结果，同键异参拒绝；业务事实、审计和 Outbox 保持同事务。应用层按创建、草稿、候选、归档和查询拆分职责，稳定 `AgentControlService` 接口未变化。
- 不可变与演进：Revision `20260816_0042` 为 `agents`、`agent_releases` 兼容扩展，并新增四张工作空间表；数据库 Trigger 拒绝修改或删除草稿修订、幂等请求和 Release，候选只允许后续节点推进状态，不能篡改来源身份。生命周期清除仅能通过既有受限 GUC 删除；存在自定义 Agent 数据时拒绝降级，避免静默丢失草稿与候选。
- 专项验收：领域单元 `3/3`，真实 PostgreSQL `4/4`，覆盖 revision 历史、陈旧 revision 冲突、幂等重放、同键异参、候选摘要、归档终止草稿、跨空间读取、Release/历史事实不可变和两个并发创建请求复用同一结果。Migration 往返、系统知识助手和 P3-02 联合 PostgreSQL 回归 `13/13`；空库升级、降级、再升级及共享表兼容通过。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `565/565`、Ruff format/lint `515` 个文件、mypy strict `515` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。相对 `stage-2-complete` 及 `HEAD` 的同主版本契约兼容检查均通过。
- 容器验收：重新构建 API、Migration、Web 和 Worker 镜像后，`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision `20260816_0042`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过；公共数据库已真实升级。本节点未增加 HTTP 路由或页面，因此不执行浏览器验收。
- 当前边界：候选只进入 `created`，不提前实现 P3-03 配置引用校验、P3-04 自动评估、P3-05 审批、P3-06 Release 生成、P3-07 服务路由或控制台页面。
- 提交：`6339144`。

### P3-03 草稿配置与校验

- 状态：已完成，提交 `773d2b9`。
- 严格配置模型：固定 Prompt、全局已发布模型运行配置、知识范围、工作流发布版本、只读工具、输出 JSON Schema、`rag-safety-v2` 和预算引用；配置对象禁止未知或缺失字段，UUID、预算和工具权限按严格类型校验，重复引用被拒绝。知识范围与工具引用排序后生成稳定 SHA-256，同一语义输入可复算为相同配置摘要。
- 资源与安全边界：创建 Agent、更新草稿和申请发布候选均通过统一校验入口，引用必须属于当前工作空间或受允许的全局发布资源，并在候选冻结前重新复核。不可变知识范围逐项复核知识库权限，工作流只能引用当前发布版本，工具只能来自五项固定只读目录；Prompt、输出 Schema 和整体配置拒绝明文 API Key、Bearer Token、私钥等凭证。
- 不可变配置事实：Prompt、知识范围和输出 Schema 使用内容寻址与幂等创建；Revision `20260816_0043` 新增 Prompt、知识范围、知识项、输出 Schema、安全策略和只读工具六类事实表，并以数据库 Trigger 拒绝更新或删除。配置 Repository 只依赖本模块公开契约，不导入其他模块私有 Infrastructure。
- 专项验收：P3-03 单元 `11/11`，P3-02/P3-03 联合单元 `14/14`，真实 PostgreSQL `8/8`，Migration、生命周期和发布治理专项 `17/17`；覆盖未知字段、缺失字段、重复引用、摘要稳定性、明文凭证、越权知识、失效模型、未发布工作流、写工具、不可变版本和候选前二次复核。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `580/580`、Ruff format/lint `522` 个文件、mypy strict `522` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器验收：重新构建并升级本地平台后，`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision `20260816_0043`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过；公共数据库已真实升级。本节点未增加 HTTP 路由或页面，因此不执行浏览器验收。
- 当前边界：本节点只建立可冻结、可复算的配置事实和发布前引用门禁，不提前实现 P3-04 测试评估、P3-05 审批、P3-06 Release 生成、外部写工具或后置能力。
- 提交：`773d2b9`。

### P3-04 测试集与自动评估

- 状态：已完成，提交 `4dbc736`。
- 固定测试事实：新增工作空间隔离、内容寻址且不可变的测试集版本和用例事实；测试集必须完整覆盖 `functional`、`authorization`、`prompt_injection`、`citation` 和 `output_contract` 五类检查，同一版本只能重放相同内容，不能原地替换。
- 确定性评估：固定离线规则策略、用例阈值和内部可信 `AgentEvaluationExecutor` 端口；失败、超时和缺失观测统一计为失败，`authorization`、`prompt_injection`、`citation` 与 `output_contract` 为不可被普通功能分抵消的硬门禁。在线 LLM Grading 和多模态图片问答保持关闭，不把 Mock 结果描述为真实 AI 质量。
- 结果与候选门禁：评估运行、检查汇总和用例结果不可变，只保存分数、时延、状态和无敏感正文的摘要；相同候选、测试集和策略已有结果时直接重放，不重复调用执行器。完整通过后候选进入 `ready_for_approval`，失败进入 `test_failed`；应用层和数据库 Trigger 均拒绝缺少匹配通过证据的绕过晋级。
- 专项验收：P3-04 单元 `5/5`，P3-02～P3-04 联合单元 `19/19`，真实 PostgreSQL、Migration 和相邻 Agent 节点联合 `17/17`，空库升级、降级、再升级与公共表兼容 `4/4`；覆盖内容漂移、结果幂等、硬门禁、超时、跳过、跨空间访问、结果篡改和直接更新候选状态。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `590/590`、Ruff format/lint `528` 个文件、mypy strict `528` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器验收：重新构建并升级本地平台后，`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision `20260816_0044`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过；公共数据库已真实升级。本节点未增加 HTTP 路由或页面，因此不执行浏览器验收。
- 当前边界：本节点证明固定规则和核心功能评估链路，不代表真实模型供应商、AI 质量或容量认证；只为 `P3-05` 预留通过证据读取门禁，不提前实现审批、Release 生成、服务路由或后置能力。
- 提交：`4dbc736`。

### P3-05 发布审批门禁

- 状态：已完成，提交 `89575ae`。
- 审批模式：复用阶段 1 既有审批引擎和最多五级运行时，不建设第二套审批系统。个人空间绑定固定内置策略，由所有者执行一级确认并允许自批；企业空间继续使用可配置多级审批策略，并继承自审限制、转交、提醒、超时通过、超时驳回和超时升级语义。
- 不可变证据：新增 `agent_approval_bindings`，冻结候选与配置摘要、评估运行与结果摘要、评估策略版本、审批实例与审批策略版本，以及 Subject/Chain 摘要。审批开始前再次校验候选和通过结果，配置或评估证据漂移时失败关闭；审批运行时通过通用 `ApprovalSubjectLifecycle` 扩展点，在同一事务内更新审批、候选、审计和 Outbox。
- 失效与防绕过：草稿产生新 revision 时，旧的待审批、已通过或已驳回候选立即进入 `superseded`；历史审批仍可完成留痕，但不能恢复或发布失效候选。Revision `20260816_0045` 增加审批绑定、固定个人策略和数据库 Trigger，拒绝直接伪造 `approval_pending`、`approved`、`rejected` 状态或修改审批绑定。
- 专项验收：真实 PostgreSQL `4/4`，审批运行时及 P3-02～P3-05 联合集成 `27/27`，空库升级、降级、再升级和公共表兼容 `4/4`，相邻单元、架构与中文注释回归 `42/42`；覆盖个人所有者审批、企业多级审批、自审限制、配置漂移、旧候选失效、跨空间拒绝、幂等重放、审计/Outbox 原子性、直接状态更新和绑定篡改。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `594/594`、Ruff format/lint `533` 个文件、mypy strict `533` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器验收：重新构建并升级本地平台后，`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision `20260816_0045`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过；公共数据库已真实升级。本节点未增加 HTTP 路由或页面，因此不执行浏览器验收。
- 当前边界：审批通过只将当前有效候选推进至 `approved`，不提前生成 `AgentRelease`、服务路由或 Runtime 绑定；这些能力从 `P3-06` 开始按顺序建设。
- 提交：`89575ae`。

### P3-06 不可变发布快照

- 状态：已完成，提交 `1d3bec3`。
- 发布快照：自定义 `AgentRelease` 只从候选绑定的不可变草稿 revision 构造，不回读当前草稿。快照按 `agent-control.v1` 固定完整配置、测试集版本、评估运行与策略、五类必需检查、审批实例与策略版本；Prompt、测试输入/回答和审批条件正文不会复制到审计或 Outbox。
- 原子性与幂等：发布事务遵循 Agent 行锁到候选行锁的固定顺序，候选唯一约束和每 Agent 单调版本保证同一候选只生成一个 Release。Release、四类来源身份、候选 `released` 终态、幂等请求、审计和 `agent.release.published` Outbox 事件同事务提交；相同键和不同键重放都返回同一 Release，不重复事件。
- 可验证与防绕过：应用写入和读取均复算规范 JSON SHA-256。Revision `20260816_0046` 为共享 Release 增加草稿 revision、评估运行和审批绑定复合外键；插入 Trigger 逐字段核对候选、配置、评估、审批、字段数量和时间语义，候选终态 Trigger 要求同事务内先存在匹配 Release，既有不可变 Trigger 继续拒绝更新和删除。系统助手 Release 保持兼容，缺少新来源字段的系统事实无需回填。
- 边界修正：全量门禁首次命中 Python 与 PostgreSQL 对时间微秒尾零的序列化差异；数据库校验改为把快照时间解析为 `timestamptz` 后做语义比较，同时保留对象字段数量和所有业务字段校验，避免把等价时间误判为篡改。
- 专项验收：真实 PostgreSQL `3/3`，P3-06 与 Migration 往返 `7/7`，P3-02～P3-06 单元、集成及 Migration 联合 `43/43`；覆盖合法发布、摘要复算、同键/不同键重放、跨空间、缺审批、直接 SQL 伪造、直接终态推进、更新和删除拒绝，以及空库升级、降级、再升级。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `597/597`、Ruff format/lint `536` 个文件、mypy strict `536` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器验收：重新构建 API、Migration、Web 和 Worker 镜像并升级公共数据库后，`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision `20260816_0046`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过。本节点未增加 HTTP 路由或页面，因此不执行浏览器验收。
- 当前边界：本节点只生成可被后续路由引用的 Release，不提前创建 `Service`、当前路由、灰度、回滚或 Runtime 装载；`P3-07` 起按顺序建设服务治理。
- 提交：`1d3bec3`。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、模型质量、真实成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机和生产容量结论。
- 镜像扫描为 `not_configured`，正式发布供应链门禁继续阻断。
- 阶段 3 尚未完成服务路由、灰度和回滚，不能把当前控制面事实描述为完整 Agent 发布平台。
- LLM Grading、多模态图片问答、真实多源连接器、Agent 外部写操作、SaaS、Go、Channel Gateway 和 Durable Run 均保持后置。

## 5. 阶段结论

`not_run`。`P3-01` 契约与安全基线、`P3-02` 生命周期事实、`P3-03` 草稿配置校验、`P3-04` 测试集与自动评估、`P3-05` 发布审批门禁和 `P3-06` 不可变发布快照已通过，当前进入 `P3-07` 服务与路由治理；在 `P3-01`～`P3-13` 全部完成、核心六项门禁和最终端到端验收通过、阶段报告与 ReleaseManifest 同步并创建 `stage-3-complete` 标签前，不给出阶段通过结论。
