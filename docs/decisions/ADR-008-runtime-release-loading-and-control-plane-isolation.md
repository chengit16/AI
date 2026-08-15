# ADR-008：Runtime 发布装载与控制面故障隔离

## 状态

已接受

## 日期

2026-08-16

## 背景

`P3-06` 已建立不可变 `AgentRelease`，`P3-07` 已建立稳定 `Service`、不可变
`ServiceRoute` 和当前路由指针，但阶段 1 系统助手执行器仍只依赖
`assistant_runs.agent_release_id` 与模型运行配置。该路径没有保存 `service_id` 和
`service_route_version`，也没有在执行前重新证明 Release 确实来自目标服务的发布路由。

Runtime 还不能直接依赖 Agent 草稿、发布候选或服务管理查询。控制面查询暂时失败时，
已解析过的发布服务应在有界时间内继续运行；缓存缺失、损坏或身份不匹配时，只能回源不可变
发布事实，不能回读草稿或可变当前配置作为降级。

## 决策驱动因素

- 每个新 Run 必须冻结唯一 `service_id`、`service_route_id`、路由版本和
  `agent_release_id`，绑定后不得改变。
- Runtime 只能读取服务、路由、Release 和运行配置等发布事实，不能依赖 Agent 控制面
  Repository 或草稿表。
- 当前路由变化必须可被后续请求观察；在途 Run 仍按已冻结的历史路由与 Release 执行。
- Valkey 只能保存可校验、可重建且有租期的派生快照，不能成为新的发布事实源。
- 控制面查询故障与发布事实损坏必须区分：前者可在可信缓存租期内降级，后者必须失败关闭。
- 阶段 1 历史 Run 不能伪造当时未记录的 ServiceRoute 身份，也不能因迁移被改绑 Release。

## 备选方案

### 方案 A：执行器继续按 `agent_release_id` 直接读取

改动最小，也能保持历史 Run 不漂移，但无法证明 Release 来自哪个服务和路由，不能为后续
灰度、回滚、服务出口和 Release 级运营提供可信绑定，因此拒绝。

### 方案 B：Runtime 每次调用服务治理应用查询

服务状态和当前路由始终最新，但执行层会依赖控制面应用与数据库查询；控制面故障将直接阻断
已发布服务，也容易把草稿查询能力带入 Runtime，因此拒绝。

### 方案 C：独立 Runtime 发布读取端口与可校验派生缓存

建立只查询发布表的窄读取端口。当前路由解析优先读取发布事实并刷新短租期缓存；已冻结 Run
按不可变绑定优先读取长租期缓存，未命中或损坏时回源发布事实。采用此方案。

## 决策

- 新建 `service_runtime` 模块，领域层定义 `RuntimeReleaseSnapshot`、发布事实 Source 和缓存
  Port；应用层只暴露“解析当前发布”和“装载精确 Run 绑定”两个用例。
- PostgreSQL Source 只读取 `services`、`service_routes`、`service_route_publications`、
  `agent_releases` 和 `agents`，不导入 Agent 或服务治理的写 Repository，也不读取草稿、候选、
  审批当前态或模型配置当前指针。
- 快照复核工作空间、服务、Route、Agent、Release、状态、Release 类型、Route 摘要和自定义
  Release 摘要。`P3-08` 只接受 `active` 单版本 Route；`canary` 和 `rollback` 的选择逻辑由
  `P3-09` 扩展，不能在本节点提前放行。
- Valkey 保存规范 JSON 和 SHA-256 信封摘要。当前路由缓存默认租期 5 分钟，精确不可变绑定
  默认租期 24 小时；缓存不可用不阻断正常数据库回源，缓存损坏会被丢弃并从发布事实重建。
- 新创建的 `assistant_runs` 同时保存 `service_id`、`service_route_id` 和
  `service_route_version`。数据库 Trigger 拒绝缺少绑定、Route 与 Release 不匹配以及运行后改绑；
  应用执行器在检索和模型调用前再次装载并复核精确发布快照。
- Migration 只为能够唯一匹配既有 Route 的历史 Run 回填完整绑定。无法唯一证明路由身份的
  升级前历史 Run 保持空绑定，可继续作为历史记录导出和查询，但不能被重新认领执行。升级后
  所有新 Run 必须具有完整绑定。
- 缓存降级只覆盖控制面发布查询故障，不承诺 PostgreSQL、Valkey、Run 状态库和模型运行事实
  同时不可用时仍能创建或完成新 Run。

## 影响

### 正面影响

- Runtime 与 Agent 草稿、候选、审批和服务管理写模型解耦，执行输入可被独立审计和复算。
- 新 Run 可以按服务、Route 和 Release 唯一追溯，当前路由变化不会改写在途或历史运行。
- 控制面发布查询短暂故障时，已预热的当前路由和精确发布绑定仍可在租期内使用。
- 缓存损坏不会降级到草稿，Valkey 丢失后可从 PostgreSQL 发布事实重建。

### 负面影响与风险

- 同一进程增加 PostgreSQL 发布读取与 Valkey 快照缓存两个 Adapter，组合根和资源清理更复杂。
- 当前路由在控制面故障期间最多允许 5 分钟陈旧窗口；服务暂停与故障同时发生时，需要依赖
  后续 Outbox 主动失效缩短窗口，该能力在 `P3-09` 路由切换节点完成。
- 升级前且无法唯一匹配 Route 的历史 Run 不具备完整路由证据，不能用推测值补齐。
- 本节点只证明发布装载和故障隔离，不交付服务访问策略执行、灰度分配或 Open API 出口。

## 验证指标

- 新 Run 的四元绑定在应用返回、数据库、审计与 Outbox 中一致，更新任一绑定字段由数据库拒绝。
- 草稿、候选、暂停服务、非活动 Agent、非 `released` Release、Route/Release 不匹配和摘要损坏
  均失败关闭。
- 已预热快照在发布 Source 故障时仍能解析；缓存损坏时从 PostgreSQL 发布事实恢复；缓存与
  Source 同时不可用时返回稳定 `SERVICE_ROUTE_UNAVAILABLE`。
- 当前路由缓存租期为 300 秒，精确绑定缓存租期为 86400 秒，配置越界时应用拒绝启动。
- 单元、真实 PostgreSQL、Migration 空库与非空库、阶段 1 助手回归、统一门禁和容器诊断通过。

## 迁移与回滚

- Revision `20260816_0048` 为 `assistant_runs` 增加三个可空兼容列、复合外键、完整性约束和
  新写入校验 Trigger，并对唯一可证明的历史 Route 执行回填。
- 部署顺序为先执行 Migration，再发布包含完整 Run 绑定的新 API；旧应用不会写新列，但在
  Migration 生效后其新 Run 会被 Trigger 拒绝，因此不支持新旧 API 长时间混跑。
- 尚无完整绑定 Run 时可降级到 `0047`。一旦产生或回填完整绑定，降级会拒绝，避免静默丢失
  Route 追溯证据；恢复方式是保留 `0048` 数据库并回滚应用，或先完成受控数据导出后再处理。
- Valkey 快照均为派生数据，可直接清除并由发布事实重新预热，不参与数据库备份恢复真值。

## 后续事项

- `P3-09` 基于当前 Route generation 增加灰度稳定分配、晋级、回滚和提交后主动缓存失效。
- `P3-10` 的页面、场景应用和 Open API 出口统一调用 Runtime 当前发布解析，并在解析前执行
  版本化访问策略与 API Key Scope 收窄。
- `P3-12` 按 `service_id + service_route_version + agent_release_id` 聚合质量、延迟、错误和成本。

## 关联内容

- [阶段 3 实施计划](../stages/stage-3-plan.md)
- [阶段 3 验证报告](../stages/stage-3-report.md)
- [ADR-007：服务治理写入权与系统助手兼容迁移](./ADR-007-service-governance-and-system-assistant-migration.md)
- [Agent 控制面基线](../../contracts/agent-control/agent-control-baseline.v1.json)
