# 阶段 4 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 4：Agent 工具执行与任务状态机 |
| 状态 | 进行中 |
| 报告日期 | 2026-08-16 |
| 当前节点 | `P4-05` 执行计划与逐步策略校验 |
| 阶段 1 `core_functional` | `passed`，继承标签 `stage-1-complete` |
| 阶段 2 可靠性 | `passed`，继承标签 `stage-2-complete` |
| 阶段 3 Agent 平台 | `passed`，继承标签 `stage-3-complete` |
| 工具执行 | `not_run` |
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
| 数据库基线 | PostgreSQL 16，Revision `20260816_0054` |
| 阶段 3 发布 | 本地 Agent 平台版本 `0.3.0`，ReleaseManifest 摘要 `60e5d17d…73e7c81` |
| 数据、模型与工具 | 只使用版本化合成数据、Mock Provider 和合成内部副作用 Adapter；不包含真实客户系统或凭证 |

## 3. 节点记录

按 [`阶段 4 实施计划`](./stage-4-plan.md) 持续追加每个节点的完成日期、实现边界、测试计数、故障演练、容器/浏览器结果、已知限制和 Git SHA。未实际执行的检查保持 `not_run` 或 `not_configured`。

### P4-01 工具执行契约与安全基线

- 状态：已完成，完成日期为 2026-08-16，实现提交为 `10b3627`。
- 交付范围：新增 [`tool-execution`](../../contracts/tool-execution/README.md) 契约域、跨语言核心事实 Schema 与 Golden Fixture、冻结基线、合成场景 Schema、24 个版本化全合成场景、15 个稳定错误码和 [ADR-011](../decisions/ADR-011-controlled-tool-execution-runtime.md)。本节点没有创建活动数据库表、OpenAPI、菜单、凭证或真实 Adapter。
- 事实模型：固定不可变工具版本、`Run`、`Step`、`Attempt`、`ToolCall`、当前策略决策、确认/审批、幂等记录、取消事实和安全结果；契约只保存规范参数摘要与结果摘要，不保存参数正文、结果正文或明文凭证。
- 状态与安全：冻结 Run、Step、Attempt、ToolCall 和确认 5 个状态机及 12 条阶段不变量；确认绑定工作空间、Run、Step、工具版本、规范参数摘要和策略版本，副作用前先持久化幂等身份，结果未知时只能人工恢复，取消/超时/租约失效后禁止新 Attempt，迟到结果不能覆盖终态。
- 首批工具：精确冻结 `knowledge.search`、`document.read_authorized_range`、`workflow.get_status`、`approval.get_status` 和 `quota.get_usage` V1，全部为内部只读、无工具凭证并复用当前资源模块权限；只额外允许合成内部写 Adapter 验证后续控制机制。
- 权限与事件：预留 6 个工具权限、5 个个人/企业共用菜单、10 个 API Operation 和 10 个集成事件；专项测试证明它们尚未进入活动资源注册表，避免 `P4-01` 用预留标识伪造可用能力。
- 合成覆盖：24 个场景覆盖个人/企业只读、草稿与 Release 越界、未知版本、跨空间、撤权与策略故障、个人确认、企业多级审批、幂等重放和冲突、结果未知、取消、迟到成功、凭证泄漏、恶意结果、任意 HTTP、不安全重试与菜单旁路；全部断言重复副作用、跨空间暴露、凭证暴露和迟到终态覆盖为零。
- 专项验证：`.venv/bin/pytest -q tests/test_p401_tool_execution_contracts.py tests/contract/test_contracts.py tests/test_engineering_guardrails.py` 为 `58/58`；Ruff、Python 注释门禁、契约兼容检查和当前工作树 Secret Scanner 均通过。
- 统一门禁：`./scripts/verify` 在允许访问本地依赖的验收环境中通过，React 为 `53/53`，Python 为 `685/685`，mypy strict 检查 `608` 个源文件；OpenAPI/类型/Registry/ReleaseManifest/SBOM 均无漂移，架构、注释、UnoCSS、供应链和生产构建通过。受限沙箱中的第一次执行因禁止访问 `127.0.0.1` 而不计为功能失败，切换到既有本地依赖验收环境后所有 PostgreSQL、Valkey、MinIO、Tika 与 OCR 集成测试通过。
- 运行诊断：`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、5 个 Worker、Scheduler 共 13 项全部通过，数据库保持 Revision `20260816_0052`。
- 验收结论：`passed`。当前只证明契约、安全基线和可重复场景完整，不代表任务状态事实、真实执行、页面或外部系统已交付；这些能力继续由 `P4-03`～`P4-13` 独立验收。

### P4-02 工具注册与版本治理

- 状态：已完成，完成日期为 2026-08-16，实现提交为 `43d099e`。
- 交付范围：新增独立 `tool_execution` 模块，原地扩展既有 `agent_tool_definitions` 唯一工具定义事实源，并新增不可变 `tool_plan_availability` 套餐映射；没有创建第二套工具定义表，也没有激活 HTTP API、菜单、工具执行、Run/Step/Attempt、凭证值或真实 Adapter。
- 定义治理：不可变工具版本完整固定 Draft 2020-12 参数与结果 JSON Schema、对应 SHA-256、完整定义摘要、读写类型、风险、Permission、Adapter、超时、重试、凭证需求、状态和合成标记；Schema 必须为关闭对象，未知字段、错误方言、任意 HTTP Adapter、非法读写/重试组合及摘要篡改全部失败关闭。
- 工作空间目录：五个冻结内部只读工具 V1 同时映射 `personal_local` 和 `enterprise_simulated`；可见目录严格取平台注册的每个工具最新 `active` 版本、当前活动套餐允许版本和当前 PDP Permission 允许结果的交集，菜单可见性不参与授权。
- 版本兼容：新目录只呈现最新活动版本，但已发布 AgentRelease 按其冻结的精确工具 ID 与版本重新验证套餐、定义状态和当前权限；新增 V2 不会让仍然有效的 V1 Release 被错误拒绝，未知或退役版本继续失败关闭。
- 数据库约束：工具定义和套餐映射均禁止 `UPDATE`/`DELETE`，跨字段 Check Constraint 阻止不安全 Adapter 与读写/重试组合；有事实时的破坏性降级被拒绝，空事实和可证明安全的既有目录支持升级、降级、再升级往返。
- 专项验证：`.venv/bin/pytest -q tests/unit/test_p402_tool_catalog.py` 为 `9/9`；真实 PostgreSQL `tests/integration/test_p402_tool_catalog_postgres.py` 为 `3/3`；P3-03、Migration 和相邻联合回归为 `45/45`。
- 统一门禁：`./scripts/verify` 通过，React 为 `53/53`，Python 为 `697/697`，mypy strict 检查 `620` 个源文件；Ruff、架构、注释、UnoCSS、契约、Registry、供应链和生产构建全部通过。
- 运行诊断：公共本地数据库已从 Revision `20260816_0052` 升级到 `20260816_0053`；重新构建启动后，`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、5 个 Worker 和 Scheduler 共 13 项全部通过。
- 验收结论：`passed`。当前只证明工具定义注册、不可变版本和工作空间目录治理成立；普通任务状态事实、工具 Adapter、执行策略、确认审批、凭证注入、幂等副作用、SSE 和页面继续由 `P4-03`～`P4-13` 独立验收。

### P4-03 Run、Step、Attempt、ToolCall 状态事实与租约

- 状态：已完成，完成日期为 2026-08-16，实现提交为 `89a5b3a`。
- 交付范围：新增 `tool_runs`、`tool_steps`、`tool_attempts`、`tool_calls` 四张工作空间业务事实表、Revision `20260816_0054`、唯一 `ToolTaskService`/`ToolTaskStore` 状态入口和 PostgreSQL Adapter；没有开放 HTTP API、菜单、真实 Adapter、凭证值或外部工具。
- Run 与 Step：Run 固定可信工作空间、Actor/账号、Service、AgentRelease、预算、幂等键、请求摘要和 Trace；Step 固定顺序号、工具 ID/版本及规范参数摘要，参数正文不进入数据库；复合外键阻断跨空间或跨版本拼接。
- Attempt 与 ToolCall：Attempt 以追加式尝试序号和租约代际保存 Worker 领取事实，ToolCall 绑定当前 Attempt、Step 和工具定义版本；`FOR UPDATE SKIP LOCKED` 保证并发领取只有一个胜者，写回必须匹配完整租约身份，迟到结果只能形成 `ignored_late_result`。
- 状态与终态：应用层和 PostgreSQL Trigger/Check Constraint 同时校验状态边、当前 Attempt、顺序、终态时间、取消事实、版本和唯一写入权；取消或超时后不创建新 Attempt，迟到成功不能覆盖已提交终态。
- 生命周期：四张业务表已纳入工作空间导出、业务清除和 Registry `v4` 精确覆盖；生命周期导出/清除回归证明新增事实不会静默遗漏，工具参数正文仍不落库。
- 安全边界：跨空间读取、取消和伪造租约均失败关闭；同一幂等键同一请求复用既有 Run，同键异请求拒绝；预算、Worker 标识、租约时长和摘要格式在应用入口收敛。
- 专项验证：`.venv/bin/pytest -q tests/unit/test_p403_tool_task_state.py` 为 `8/8`；`tests/integration/test_p403_tool_task_state_postgres.py` 为 `4/4`；P4-01/P4-02/Migration 联合回归为 `50/50`。
- 统一门禁：`./scripts/verify` 通过，React 为 `53/53`，Python 为 `709/709`，mypy strict 检查 `627` 个源文件；OpenAPI、权限资源、ReleaseManifest、契约兼容、架构依赖、注释、UnoCSS、生产构建和 Secret Scanner 均无漂移。
- 运行诊断：公共本地实例按统一启动流程升级到 Revision `20260816_0054`；`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、5 个 Worker 和 Scheduler 共 13 项全部通过。
- 验收结论：`passed`。当前只证明普通工具任务的状态事实、租约和唯一写入权成立；五个内部只读工具 Adapter、执行计划、确认/审批、凭证注入、幂等副作用、SSE、页面和联合演练继续由 `P4-04`～`P4-13` 独立验收。

### P4-04 五个内部只读工具与统一 Adapter

- 状态：已完成，完成日期为 2026-08-16，实现提交为 `053998e`。
- 交付范围：新增 `ToolAdapterService`、内部只读 Adapter 端口、五个冻结工具的封闭适配类和责任模块函数装配入口；继续使用 `P4-02` 唯一工具目录解析精确版本，没有新建数据库表、HTTP API、菜单、任意 HTTP/SQL/文件系统入口、凭证或真实外部连接器，数据库保持 Revision `20260816_0054`。
- 调用前门禁：只允许 `internal_read`、`read`、无凭证且非合成的已登记工具版本；参数按对应 Draft 2020-12 Schema 校验，未知字段和错误类型失败关闭；文档、工作流运行和审批实例目标必须落在当前工作空间级或资源级授权范围内，空资源集合不退化为全量授权。
- 责任边界：五个 Adapter 只接收可信 `RequestContext` 和已校验参数，装配入口允许组合根注入原知识、文档、工作流、审批和配额模块的公开只读处理函数；当前节点不直接导入其他模块私有 Repository，也不建立网络或任意查询能力。模型候选意图、AgentRelease 允许列表、冻结 Step 与实际任务入口由 `P4-05` 接续。
- 结果安全：返回值必须匹配冻结输出 Schema 且为可规范序列化 JSON；在进入后续模型上下文前再次执行字段遮罩、256 KiB 大小、敏感字段/凭证格式和中英文 Prompt Injection 检查，失败统一拒绝；通过结果生成稳定 SHA-256 和四项检查回执，不把原始结果写入工具任务事实。
- 专项验证：`.venv/bin/pytest -q tests/unit/test_p404_internal_read_adapters.py` 为 `5/5`；真实 PostgreSQL `tests/integration/test_p404_internal_read_adapters_postgres.py` 为 `1/1`，验证个人套餐、当前 PDP、五个数据库冻结定义、输入/输出 Schema 和 Adapter 分发闭环。
- 统一门禁：`./scripts/verify` 通过，React 为 `53/53`，Python 为 `715/715`，mypy strict 检查 `632` 个源文件；OpenAPI、权限资源、ReleaseManifest、契约兼容、架构依赖、注释、UnoCSS、生产构建和 Secret Scanner 均无漂移。
- 运行诊断：`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、5 个 Worker 和 Scheduler 共 13 项全部通过，数据库保持 Revision `20260816_0054`。
- 验收结论：`passed`。当前证明五个冻结内部只读工具具备统一且失败关闭的 Adapter 边界，不代表模型已经能够生成或执行工具计划；执行计划、逐步策略复核、确认/审批、凭证注入、幂等副作用、SSE、页面和联合演练继续由 `P4-05`～`P4-13` 独立验收。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、模型质量、成本或数据政策结论。
- 当前没有真实外部业务 API、客户工具凭证或连接器；阶段 4 只使用合成内部 Adapter 验证控制机制。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机和生产容量结论。
- 镜像扫描为 `not_configured`，正式发布供应链门禁继续阻断。
- LLM Grading、多模态图片问答、真实多源连接器、SaaS、Go、Channel Gateway 和 Durable Run 均保持后置。

## 5. 阶段结论

`not_run`。`P4-01`～`P4-04` 已通过，但尚未给出阶段 4 工具执行整体通过结论；在 `P4-01`～`P4-13` 全部完成、未授权工具拒绝、未确认副作用拒绝、幂等零重复、步骤/尝试可追溯、凭证零泄漏和安全取消六项门禁通过、阶段报告与 ReleaseManifest 同步并创建 `stage-4-complete` 标签前，不关闭阶段。
