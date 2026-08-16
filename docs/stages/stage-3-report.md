# 阶段 3 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 3：Agent 控制面与服务发布 |
| 状态 | 进行中 |
| 报告日期 | 2026-08-16 |
| 当前节点 | `P3-13` 联合验收与阶段关闭 |
| 阶段 1 `core_functional` | `passed`，继承标签 `stage-1-complete` |
| 阶段 2 可靠性 | `passed`，继承标签 `stage-2-complete` |
| Agent 控制面契约基线 | `passed` |
| Agent 控制面 | `in_progress`，生命周期、配置校验、自动评估、审批、不可变 Release、服务治理、Runtime 隔离、灰度回滚、统一服务出口、控制台与运营监控已通过 |
| 服务发布与回滚 | `passed`，自定义知识 Agent、场景应用和 Open API 三类出口已接入同一发布路由与安全门禁 |
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
| 数据库基线 | PostgreSQL 16，Revision `20260816_0052` |
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

### P3-07 服务与版本化路由治理

- 状态：已完成，提交 `2cf0de8`。
- 写入权与领域边界：接受 `ADR-007`，新增独立 `service_governance` 模块，独占 `Service`、不可变访问策略、不可变 `ServiceRoute`、当前 Route 指针和服务幂等请求的写入规则。自定义知识服务从 `draft v1` 在同一事务写入策略、首个活动 Route 与指针后激活为 `active v2`；创建和更新均冻结原始响应快照，后续幂等重放不受当前状态漂移影响。
- 状态与访问策略：支持 `workspace` 和 `restricted` 两类版本化策略，限制策略的部门和账号必须是当前工作空间内的活动主体；名称、暂停、恢复、归档和策略变更使用服务乐观版本并遵循冻结状态机。服务、策略、路由、幂等请求、审计和 `service.state.changed` Outbox 保持同事务，事件不携带访问主体清单。
- 系统助手兼容：阶段 1 `agent_publications` 继续作为旧问答链路兼容指针，助手 Unit of Work 通过注入的 `ServiceRepository` 在原事务创建或追加固定 `system-knowledge` 服务 Route。运行配置变化后两个当前指针指向同一 Release，旧 Run 仍绑定旧 Release；Route 同步同时推进 `Service.version`，保证 Outbox `aggregate_version` 严格单调。
- 数据库与迁移：Revision `20260816_0047` 新增五张工作空间表、复合外键、延迟循环约束和状态/策略/Route/当前指针校验 Trigger。历史策略、Route 和幂等请求禁止更新或普通删除；生命周期受限事务仍可清除。升级只回填固定 `agent_key=system_knowledge` 的当前系统助手，其他系统 Agent 保持原样；存在自定义服务、历史 Route 或控制请求时拒绝不安全降级。
- 专项验收：P3-07 单元 `4/4`、真实 PostgreSQL `3/3`，Migration 空库往返与带既有系统助手的 `0046 → 0047` 非空回填 `5/5`，P3-02～P3-07、阶段 1 系统助手、检索规划、Migration 和生命周期联合回归 `60/60`；覆盖幂等快照、策略版本、暂停/恢复、跨空间、跨 Agent Route、历史篡改、非法状态和 generation 跳跃拒绝。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `605/605`、Ruff format/lint、mypy strict `552` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器验收：使用最终工作树重新构建 API、Migration、Web 和 Worker 镜像，公共数据库保持 Revision `20260816_0047`；`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过。本节点未增加 HTTP 路由或页面，因此不执行浏览器验收。
- 当前边界：本节点只建立单版本活动 Route 和服务控制面事实，不提前实现 `P3-08` Runtime 快照装载与控制面故障隔离、`P3-09` 灰度/晋级/回滚或 `P3-10` 服务出口。
- 提交：`2cf0de8`。

### P3-08 Runtime 隔离路由

- 状态：已完成，提交 `328bcca`。
- 发布读取边界：接受 `ADR-008`，新增独立 `service_runtime` 模块，只从 `Service`、当前或精确 `ServiceRoute`、`AgentRelease` 和 `Agent` 发布事实构造 Runtime 快照，不导入草稿、候选、审批当前态或服务治理写 Repository。快照逐项复核工作空间、状态、Release 类型、运行配置、Route 摘要和自定义 Release 摘要；P3-09 前只接受当前 `active` 单版本 Route。
- 故障隔离与缓存：当前 Route 正常路径优先读取 PostgreSQL，并写入默认 300 秒短租期缓存；Run 精确绑定优先读取默认 86400 秒长租期缓存。两个键使用规范 JSON 和 SHA-256 信封摘要，历史绑定写入不能覆盖当前 Route；缺失、损坏或身份错位缓存只能删除并回源发布事实。缓存不可用不阻断正常数据库读取，发布 Source 与可信缓存同时不可用时稳定返回 `SERVICE_ROUTE_UNAVAILABLE`。
- Run 唯一绑定：新 Run 在消息、审计和 `assistant.run.queued` Outbox 同事务冻结 `service_id`、`service_route_id`、`service_route_version` 与 `agent_release_id`；执行器在检索和模型调用前重新装载精确发布快照，并使用 Release 冻结的运行配置。暂停服务在应用层拒绝新 Run，已冻结在途 Run 仍允许收敛终态；跨空间、当前 Route 与 Release 错配、历史 Route 新写入和运行后改绑由数据库最终拒绝。
- 数据库与兼容：Revision `20260816_0048` 为 `assistant_runs` 增加三个兼容可空 Route 字段、复合外键、索引和防改绑 Trigger。Migration 只为能够唯一匹配 Route 的升级前历史 Run 回填，无法证明的记录保持完整空绑定且不能重新执行；升级后的任何新 Run 必须完整绑定。一旦存在完整绑定即拒绝降级到 `0047`。OpenAPI 以可选可空字段保持历史响应兼容，React 与 Python 类型由冻结契约重新生成。
- 专项验收：P3-08 单元 `12/12`、阶段 1 助手执行器回归 `1/1`、真实 PostgreSQL `2/2`、完整 Migration 空库往返与 `0047 → 0048` 非空升级 `6/6`；覆盖正常装载、缓存预热、Source 故障续跑、历史绑定不污染当前键、损坏与身份错位恢复、双故障失败关闭、暂停服务、在途收敛、跨空间、历史 Route、Release 错配、改绑拒绝和无法证明的历史记录。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `620/620`、Ruff format/lint `564` 个文件、mypy strict `564` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器验收：使用最终工作树重新构建 API、Migration、Web 和 Worker 镜像，公共数据库真实升级至 Revision `20260816_0048`；`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过。本节点未增加页面，因此不执行浏览器验收。
- 当前边界：当前 Route 在发布 Source 故障期间最多使用 300 秒可信缓存，服务暂停与 Source 故障同时发生时的主动失效由 P3-09 通过提交后事件完成。本节点不提前实现灰度稳定分配、晋级、回滚、服务出口或 Release 运营监控。
- 提交：`328bcca`。

### P3-09 灰度发布与回滚

- 状态：已完成，提交 `6aa5a21`。
- 稳定分配与隐私边界：接受 `ADR-009`，使用 `SHA-256(service_id + assignment_key) % 100` 形成固定百分位，同一服务和规范分配键重复解析不漂移，提高比例时既有低位灰度群体不重新洗牌。原始分配键不进入 Route、缓存键、日志、审计或 Outbox；Runtime Source 在同一发布查询中选择 primary 或 canary Release，Loader 再次复核 Route 结构、摘要和百分位结果。
- 追加式发布控制：灰度、晋级和回滚均创建新的不可变 `ServiceRoute`，不修改 `AgentRelease` 或历史 Route。Route、publication、Service 版本、幂等请求、审计和 Outbox 在同一事务提交；`expected_generation` 与受锁当前态保证两个并发发布者只有一个胜者。回滚 `canary` 恢复稳定 primary，回滚已晋级 `active` 恢复紧邻前序 Route 的稳定 primary，并使用新的 `rollback` Route 保存事实。
- 缓存与在途隔离：current 缓存升级为 `runtime-current:v2` 的固定 `0..99` 桶，最多每服务 100 个短租期键；发布、服务状态和系统助手自动 Route 变化均在事务提交后删除全部分桶及旧版单键，缓存失效失败只记录告警，不撤销数据库事实。新 Run 观察切换后的 Route，已冻结在途 Run 继续按原 Route、Route Version 和 Release 精确装载并完成。
- 数据库与迁移：Revision `20260816_0049` 扩展灰度、晋级和回滚幂等操作，强化 Route 中灰度 Release 的工作空间、Agent、发布状态和快照门禁，并让 Run Trigger 只接受当前 `canary` 的 primary/canary Release 或当前 `active`、`rollback` 的唯一 primary。跨空间、跨 Agent、失效 Release、断裂 Route 链、跳跃 generation、非法 Run 绑定和绑定修改继续失败关闭；存在 P3-09 Route 或控制请求时拒绝降级到 active-only Runtime。
- 专项验收：P3-07～P3-09 单元 `18/18`，P3-09 真实 PostgreSQL `3/3`，P3-07/P3-08 PostgreSQL 与完整 Migration 往返 `11/11`；覆盖固定分桶、100 桶主动失效、系统助手自动 Route 同步、灰度选择、晋级、回滚、Release 摘要不变、在途收敛、并发唯一胜者、失败事务零 Route/幂等请求/事件副作用和不安全降级拒绝。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `625/625`、Ruff format/lint `568` 个文件、mypy strict `568` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、权限注册表、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器验收：通过 `./platform start` 使用最终工作树重建 API、Migration、Web 和 Worker 镜像，公共数据库真实升级至 Revision `20260816_0049`；`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过。本节点未增加 HTTP 路由或页面，因此不执行浏览器验收。
- 当前边界：本节点只完成内部路由用例和 Runtime 选择，不提前开放自定义知识 Agent、场景应用或 Open API 出口；三类出口由 `P3-10` 执行访问策略、API Key Scope、配额和限流后接入。基于 Release 质量、错误、延迟和成本指标的异常自动阻断仍由 `P3-12` 建设。
- 提交：`6aa5a21`。

### P3-10 统一服务出口

- 状态：已完成，提交 `0822475`。
- 统一出口：接受 `ADR-010`，新增独立 `service_delivery` 模块，把 `custom_knowledge_agent`、`scenario_application` 和 `open_api` 三类 Service 接入同一访问策略、Runtime 当前路由、月度配额、隐藏调用会话、Assistant Run 和执行器。浏览器只允许调用前两类页面 surface，Open API Key 只允许调用 `open_api`，surface 错配在产生 Run 前失败关闭；不引入 Channel Gateway 或外部写操作。
- 身份与权限：资源注册表 19 激活 `service.definition.read` 及创建、查询、SSE 三个 Operation，形成 86 项权限、122 个 API、105 个菜单和 115 个绑定。Open API Key 同时支持权限级 Scope 和严格的 `service.definition.read.{32位小写hex}` 服务级 Scope；Scope 只能收窄账号既有权限。API Key Actor 独立承担幂等、读取、审计和 Run 归属，创建账号仍负责服务访问策略与内部 RAG 的 RBAC/ABAC；检索前清除 API Key Scope 和旧授权结论并重新决策，服务 Scope 不能冒充知识或文档权限。
- 调用事实与恢复：`conversation_kind=service_invocation` 的隐藏会话不进入普通私有会话列表，`requested_by_actor_id` 让两个 API Key 使用相同幂等键仍相互隔离。POST、HTTP 快照和 SSE 都读取同一 Actor 的同一 Run；`Last-Event-ID` 复用阶段 2 PostgreSQL 事件事实和 Valkey 无正文唤醒，重连不创建 Run、不重复执行模型。部门限制策略覆盖后代部门，未分配成员和跨 Actor 读取均稳定拒绝。
- 配额与限流：问答月度配额在 Run 创建事务内原子消费，超额请求不产生会话或 Run。Valkey Lua 固定窗口按工作空间、Service 和 Actor 的不可逆摘要计数，相同 Actor 与幂等键重放不重复占用；超过窗口返回 `SERVICE_RATE_LIMITED`，限流事实不可用时以 `SERVICE_RATE_LIMIT_UNAVAILABLE` 默认拒绝。
- 数据库与迁移：Revision `20260816_0050` 为会话增加显式类型，为 Run 增加独立 Actor 归属并回填历史账号身份；数据库 Trigger 拒绝新 Run 缺少完整 Actor、调用后改绑和服务调用会话伪装为普通会话。既有角色获得服务读取权限，当前菜单发布快照原子升级到 Registry 19；存在隐藏调用事实时拒绝降级到旧账号归属模型。
- 专项验收：P3-10 单元 `14/14`、真实 PostgreSQL/Valkey/HTTP/SSE `5/5`、完整 Migration 往返 `7/7`；P3-07～P3-10 真实服务治理、Runtime、灰度和出口联合 `13/13`，身份、资源注册表、Assistant 与 SSE 相邻回归 `22/22`。覆盖三类 surface、错配拒绝、服务级 Scope、Actor 幂等与读取隔离、隐藏会话、配额、部门后代、Valkey 限流、HTTP/SSE 断点恢复、Actor 防改绑、历史角色和菜单快照升级及不安全降级。
- 统一门禁：`./scripts/verify` 通过 React `42/42`、Python `649/649`、Ruff format/lint `582` 个文件、mypy strict `582` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、Registry 19、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器验收：使用最终工作树重建 API、Migration、Web 和 Worker 镜像，公共数据库真实升级至 Revision `20260816_0050`；`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、五个 Worker Lane 和 Scheduler 共 13 项全部通过。本节点没有新增控制台页面，因此不执行真实浏览器页面验收，页面交付进入 `P3-11`。
- 当前边界：本节点只交付可供页面和客户端消费的统一后端出口，不提前建设 Agent 控制台或 Release 运营监控。真实模型供应商、AI 质量、容量、LLM Grading、多模态图片问答、真实连接器、Agent 外部写操作、SaaS、Go、Channel Gateway 和 Durable Run 继续保持既定边界。
- 提交：`0822475`。

### P3-11 Agent 与服务发布控制台

- 状态：已完成，提交 `a5e205b`。
- 控制台与授权：Agent 和服务页面使用 React 19、TanStack Query、Ant Design 6 与 UnoCSS 接入现有工作空间壳层，所有页面继续由菜单、页面权限和 API 权限统一控制；前端隐藏不构成安全边界。Registry 20 激活 17 项所有者权限、5 项成员只读权限和 16 个控制台 API 绑定，并原子升级个人与企业空间的菜单发布快照；当前注册表共 103 项权限、138 个 API、123 个菜单和 131 个绑定。
- Agent 发布流程：页面支持 Agent 创建、草稿乐观锁保存、历史 revision、候选申请、固定五类测试、个人所有者或企业多级审批、发布及不可变 Release 查询。测试执行和审批状态继续复用 P3-04/P3-05 领域服务，页面不能绕过候选摘要、硬门禁、审批绑定或 Release 不可变约束。
- 首个 Agent 基础配置：创建请求必须在完整 `configuration` 与显式 `use_starter_configuration` 之间二选一。基础模式在同一事务生成内容寻址 Prompt、空知识范围、基础 Answer JSON Schema，引用当前已发布 Runtime、匹配的活动安全策略、空只读工具和保守预算，最终草稿只保存规范化完整配置；基础资源、Agent、首个草稿、审计和 Outbox 原子提交。本地 Mock 环境缺少 Runtime 时允许显式自举，非本地环境继续失败关闭；高级用户仍可切换完整 JSON 模式。
- 服务发布流程：页面支持自定义知识服务创建、工作空间或限制范围策略编辑、暂停、恢复、归档、10%～90% 灰度、晋级和一键回滚；所有写操作携带幂等键、预期版本或 generation，由后端重新执行工作空间授权、Release 归属、Route 状态机和并发控制，页面不缓存或伪造发布事实。
- 数据库与契约：Revision `20260816_0051` 激活控制台授权绑定与菜单快照，存在本节点授权或升级快照时拒绝不安全降级；OpenAPI、Python 契约和 React 类型由同一冻结契约生成。`jsonschema[format]` 移入生产依赖，保证容器内确定性测试执行器具备与开发环境一致的 Schema 校验能力。
- 专项验收：Agent 控制台真实 PostgreSQL HTTP 全链路 `1/1`，共享契约 `38/38`；覆盖新个人空间基础配置创建首个 Agent、完整草稿持久化、两次 revision、两次候选、五类测试、所有者审批、两版 Release、服务创建、10% 灰度、晋级、回滚、暂停和恢复。合成验收结果为 Agent `cbbf97db-5799-4e61-92d2-23796bcbd68b`、Service `fe606036-f266-42fa-8bf9-599a1cf56ea3` 和最终 Route generation 4，不包含真实个人或企业资料。
- 统一门禁：`./scripts/verify` 通过 React `50/50`、Python `652/652`、Ruff format/lint、mypy strict `591` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、Registry 20、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器与浏览器验收：使用最终工作树重建 API、Migration、Web 和 Worker 镜像，公共数据库真实升级至 Revision `20260816_0051`；`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、五个 Worker Lane 和 Scheduler 共 13 项全部通过。`1440×900` 下 Agent 与服务页面无页面级横向溢出，Release 宽表格仅在局部容器滚动；`390×844` 下两页宽度均保持 390px，完整 JSON 创建弹窗为 `374×693` 且完整位于视口内。
- 当前边界：本节点不建设 AgentRelease 质量、时延、错误和成本运营视图或异常自动阻断，该能力由 `P3-12` 实施；真实模型供应商、AI 质量、容量、LLM Grading、多模态图片问答、真实连接器、外部写工具、SaaS、Go、Channel Gateway 和 Durable Run 继续保持既定边界。
- 提交：`a5e205b`。

### P3-12 AgentRelease 运营监控与晋级门禁

- 状态：已完成，提交 `6ca4c51`。
- 运营读模型：新增独立 `agent_operations` 模块和只读 `GET /api/v1/workspaces/{workspace_id}/agent-release-operations`。PostgreSQL 在同一只读快照中按工作空间、Service、当前 Route、Release 和 1～168 小时窗口聚合 Run 终态、成功/失败、模型降级、P95 延迟、总成本、平均/最高单次成本、人工反馈与离线评估；成本只通过同一工作空间和 Trace 归属 Run，不返回 Prompt、消息正文、Run、Actor、Trace 或其他高基数主体标识。
- 版本对比与样本语义：当前正式 Release 始终作为主版本；存在灰度时比较灰度 Release，否则比较上一 Route 的 Release。终态最少样本为 5，反馈最少样本为 3；无样本比例保持空值并显示“尚未测量”，不使用 0 冒充已测结果。AI 质量保持 `not_configured`，在线 LLM Grading 固定关闭，人工反馈不足不冒充质量已测。
- 阈值与晋级：固定策略 `agent-operations-v1` 使用错误率不高于 10%、降级率不高于 20%、P95 不高于 10 秒、有帮助率不低于 70%、单次成本不超过 Release 冻结预算，以及相对主版本错误率最多回归 5 个百分点、降级率最多回归 10 个百分点、P95 和平均成本最多为 1.5 倍。样本不足、Route/主版本/候选身份漂移、任一阻断阈值异常或门禁实现缺失均失败关闭；晋级审计与 Outbox 只保存策略版本和 SHA-256 证据摘要。
- 权限与菜单：Registry 21 新增 `agent.operations.read`、`AgentOperationsPage`、页面/动作菜单和查询 API 绑定，当前注册表共 104 项权限、139 个 API、125 个菜单和 132 个绑定。默认只向 `workspace_owner` 授权，企业空间可通过既有自定义角色显式授予；页面隐藏仍不构成安全边界。Revision `20260816_0052` 幂等回填个人/企业所有者、原子升级现有菜单发布快照并新增 `model_invocations(workspace_id, trace_id)` 聚合索引，存在新授权或升级快照时拒绝不安全降级。
- 运营页面：React 19、TanStack Query、Ant Design 6 和 UnoCSS 新增 `/workspace/agent-operations`，服务和 24/48/168 小时窗口进入 URL；页面展示晋级结论、样本门槛、正式/灰度或上一版本同口径指标、结构化阈值告警、策略版本和可复制证据摘要。服务或报告查询失败后不保留旧 Route，宽表格只在局部容器滚动。
- 自动验收：P3-12 单元、API 和 Registry 专项 `11/11`，真实 PostgreSQL 聚合 `1/1`，P2-10/P3-11/P3-12 联合回归 `5/5`；`./scripts/verify` 通过 React `53/53`、Python `658/658`、Ruff format/lint、mypy strict `605` 个源文件、前后端架构、中文注释、UnoCSS、OpenAPI/生成契约、Registry 21、Secret Scanner、SBOM、许可证、ReleaseManifest、开发供应链和生产构建。
- 容器与浏览器验收：最终工作树重建 API、Migration、Web、Tika 和 Worker 镜像，公共数据库真实升级至 Revision `20260816_0052`；`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、五个 Worker Lane 和 Scheduler 共 13 项全部通过。真实全合成 P3-11 个人空间可见运营菜单和历史版本对比；`1440×900` 的 document/body 均保持 1440px，`390×844` 的 document/body/main 均保持 390px，无页面级横向溢出；两张表以 750px 和 850px 内容宽度局部滚动，48 小时切换同步更新 URL 与报告窗口，浏览器控制台无错误。
- 当前边界：真实模型供应商、真实 AI 质量、容量、LLM Grading、多模态图片问答、真实连接器、外部写工具、SaaS、Go、Channel Gateway 和 Durable Run 继续保持既定边界。运营指标和告警已经形成确定性闭环，但不能据此宣称真实模型质量或生产容量已通过。
- 提交：`6ca4c51`。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、模型质量、真实成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机和生产容量结论。
- 镜像扫描为 `not_configured`，正式发布供应链门禁继续阻断。
- AgentRelease 运营指标和确定性晋级门禁已完成，但真实模型供应商和 `ai_quality` 仍为 `not_configured`，不能宣称真实模型质量已通过。
- LLM Grading、多模态图片问答、真实多源连接器、Agent 外部写操作、SaaS、Go、Channel Gateway 和 Durable Run 均保持后置。

## 5. 阶段结论

`not_run`。`P3-01` 契约与安全基线、`P3-02` 生命周期事实、`P3-03` 草稿配置校验、`P3-04` 测试集与自动评估、`P3-05` 发布审批门禁、`P3-06` 不可变发布快照、`P3-07` 服务与路由治理、`P3-08` Runtime 隔离路由、`P3-09` 灰度发布与回滚、`P3-10` 统一服务出口、`P3-11` Agent 控制台和 `P3-12` AgentRelease 运营监控已通过，当前进入 `P3-13` 联合验收与阶段关闭；在核心六项门禁和最终端到端验收通过、阶段报告与 ReleaseManifest 同步并创建 `stage-3-complete` 标签前，不给出阶段通过结论。
