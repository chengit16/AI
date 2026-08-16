# 阶段 4 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 4：Agent 工具执行与任务状态机 |
| 状态 | 进行中 |
| 报告日期 | 2026-08-16 |
| 当前节点 | `P4-02` 已完成，下一节点为 `P4-03` 任务状态事实 |
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
| 数据库基线 | PostgreSQL 16，Revision `20260816_0053` |
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
- 验收结论：`passed`。当前只证明契约、安全基线和可重复场景完整，不代表工具注册、任务表、真实执行、页面或外部系统已交付；这些能力继续由 `P4-02`～`P4-13` 独立验收。

### P4-02 工具注册与版本治理

- 状态：已完成，完成日期为 2026-08-16，实现提交待回填。
- 交付范围：新增独立 `tool_execution` 模块，原地扩展既有 `agent_tool_definitions` 唯一工具定义事实源，并新增不可变 `tool_plan_availability` 套餐映射；没有创建第二套工具定义表，也没有激活 HTTP API、菜单、工具执行、Run/Step/Attempt、凭证值或真实 Adapter。
- 定义治理：不可变工具版本完整固定 Draft 2020-12 参数与结果 JSON Schema、对应 SHA-256、完整定义摘要、读写类型、风险、Permission、Adapter、超时、重试、凭证需求、状态和合成标记；Schema 必须为关闭对象，未知字段、错误方言、任意 HTTP Adapter、非法读写/重试组合及摘要篡改全部失败关闭。
- 工作空间目录：五个冻结内部只读工具 V1 同时映射 `personal_local` 和 `enterprise_simulated`；可见目录严格取平台注册的每个工具最新 `active` 版本、当前活动套餐允许版本和当前 PDP Permission 允许结果的交集，菜单可见性不参与授权。
- 版本兼容：新目录只呈现最新活动版本，但已发布 AgentRelease 按其冻结的精确工具 ID 与版本重新验证套餐、定义状态和当前权限；新增 V2 不会让仍然有效的 V1 Release 被错误拒绝，未知或退役版本继续失败关闭。
- 数据库约束：工具定义和套餐映射均禁止 `UPDATE`/`DELETE`，跨字段 Check Constraint 阻止不安全 Adapter 与读写/重试组合；有事实时的破坏性降级被拒绝，空事实和可证明安全的既有目录支持升级、降级、再升级往返。
- 专项验证：`.venv/bin/pytest -q tests/unit/test_p402_tool_catalog.py` 为 `9/9`；真实 PostgreSQL `tests/integration/test_p402_tool_catalog_postgres.py` 为 `3/3`；P3-03、Migration 和相邻联合回归为 `45/45`。
- 统一门禁：`./scripts/verify` 通过，React 为 `53/53`，Python 为 `697/697`，mypy strict 检查 `620` 个源文件；Ruff、架构、注释、UnoCSS、契约、Registry、供应链和生产构建全部通过。
- 运行诊断：公共本地数据库已从 Revision `20260816_0052` 升级到 `20260816_0053`；重新构建启动后，`./platform doctor` 的 Web、API、MinIO、Tika、PostgreSQL、Revision、Valkey、5 个 Worker 和 Scheduler 共 13 项全部通过。
- 验收结论：`passed`。当前只证明工具定义注册、不可变版本和工作空间目录治理成立；普通任务状态事实、工具 Adapter、执行策略、确认审批、凭证注入、幂等副作用、SSE 和页面继续由 `P4-03`～`P4-13` 独立验收。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、模型质量、成本或数据政策结论。
- 当前没有真实外部业务 API、客户工具凭证或连接器；阶段 4 只使用合成内部 Adapter 验证控制机制。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机和生产容量结论。
- 镜像扫描为 `not_configured`，正式发布供应链门禁继续阻断。
- LLM Grading、多模态图片问答、真实多源连接器、SaaS、Go、Channel Gateway 和 Durable Run 均保持后置。

## 5. 阶段结论

`not_run`。`P4-01` 契约与安全基线已经通过，但尚未给出阶段 4 工具执行整体通过结论；在 `P4-01`～`P4-13` 全部完成、未授权工具拒绝、未确认副作用拒绝、幂等零重复、步骤/尝试可追溯、凭证零泄漏和安全取消六项门禁通过、阶段报告与 ReleaseManifest 同步并创建 `stage-4-complete` 标签前，不关闭阶段。
