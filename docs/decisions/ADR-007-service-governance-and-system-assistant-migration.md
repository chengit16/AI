# ADR-007：服务治理写入权与系统助手兼容迁移

## 状态

已接受

## 日期

2026-08-16

## 背景

阶段 1 已使用 `agents`、不可变 `agent_releases` 和可变 `agent_publications` 支撑系统知识助手。阶段 3 又引入自定义 Agent、经过测试和审批的不可变 Release，以及稳定 `service_id`、版本化 `ServiceRoute` 和访问策略。若继续让助手模块、Agent 控制面和未来 Runtime 分别维护发布指针，同一 Release 可能出现多个互相漂移的当前版本，路由变更也无法形成统一审计链。

迁移还必须保持阶段 1 行为：现有系统助手仍能按当前模型运行配置生成新 Release，历史会话和 Run 继续绑定原 `agent_release_id`，本节点不能要求调用方切换到尚未交付的阶段 3 Runtime。

## 决策驱动因素

- `Service`、访问策略、历史路由和当前路由指针必须具有唯一数据写入权。
- 任何路由只能引用同一工作空间、同一 Agent 的有效不可变 `AgentRelease`。
- 访问策略和路由历史只能追加版本，不能原地覆盖或删除。
- 服务状态变化必须遵循冻结状态机，并与审计、Outbox 和幂等事实同事务提交。
- 阶段 1 的 `agent_publications` 和 `assistant_runs.agent_release_id` 仍需兼容，迁移不能改写历史 Run。
- `P3-07` 不提前实现 Runtime 装载、灰度、晋级或回滚。

## 备选方案

### 方案 A：继续以 `agent_publications` 作为全部服务路由

复用成本最低，但该表只能保存一个 Agent 的当前 Release，不能表达稳定服务身份、访问策略、服务状态、路由历史或后续灰度。直接扩展还会把阶段 1 兼容字段和阶段 3 路由职责永久耦合，因此拒绝。

### 方案 B：助手模块和 Agent 控制面各自写入服务表

两个模块都能在本地事务内完成写入，但会形成多写者，服务状态机、摘要计算和数据库错误映射容易漂移，也违反模块不得写入其他模块私有数据的约束，因此拒绝。

### 方案 C：独立服务治理模块与受限兼容端口

由 `service_governance` 独占服务事实的领域模型、应用规则和 PostgreSQL Adapter。自定义服务通过稳定应用服务创建和更新；系统助手的 Unit of Work 注入同一领域 Repository，只提交系统助手所需的幂等“确保当前系统服务路由”命令。采用此方案。

## 决策

- 新建 `service_governance` 模块，独占 `services`、`service_access_policy_versions`、`service_routes`、`service_route_publications` 和 `service_control_requests` 的写入规则。
- 自定义服务创建时先形成 `draft` 事实，再在同一事务写入访问策略版本、初始 `active` 路由和当前指针，最后按状态机激活服务。失败不会留下可路由半成品。
- `ServiceRoute` 与访问策略版本只允许插入；当前路由通过独立 `service_route_publications` 指针维护。路由摘要覆盖服务、版本、模式、Release 和前序路由身份。
- 初始 `P3-07` 只允许 `active` 单版本路由。`canary`、`promote` 和 `rollback` 的应用命令在 `P3-09` 实现，但数据库结构从本节点开始保留对应表达能力。
- 访问策略首期支持 `workspace` 和 `restricted`。`workspace` 不携带主体名单；`restricted` 至少包含一个当前工作空间内的部门或活动账号。策略变更生成新版本并更新服务指针。
- 系统助手继续写入 `agent_publications` 以保持阶段 1 行为，同时通过注入的 `ServiceRepository` 确保固定 `service_key=system-knowledge` 的系统服务及当前 Route 与 Release 同步。该兼容路径不能创建自定义服务或灰度路由。
- Migration 为升级时已存在的系统助手发布指针回填系统服务、工作空间访问策略和初始路由；不修改任何 `assistant_runs`、历史 Release 或会话记录。
- 服务治理写操作只接受服务端认证链构造的浏览器主体，并要求可信工作空间或目标 `service_id` 资源范围。数据库复合外键和 Trigger 再次拒绝跨空间、跨 Agent、无效状态迁移和历史篡改。

## 影响

### 正面影响

- 自定义服务和系统助手共享同一服务治理事实，后续 Runtime 只需读取稳定 `service_id` 和版本化路由。
- 当前指针与不可变历史分离，为灰度、回滚和并发 generation 控制保留清晰边界。
- 系统助手迁移不要求重写历史 Run，也不改变阶段 1 会话和问答接口。
- 服务、策略、路由、幂等、审计和 Outbox 可以在一个 PostgreSQL 事务内提交。

### 负面影响与残余风险

- 在 `P3-08` 切换 Runtime 前，系统助手仍同时保留 `agent_publications` 和服务路由指针；专项测试必须证明两者指向同一 Release。
- 访问策略当前只保存和校验治理事实，真正的服务调用授权在 `P3-10` 出口接入时执行。
- 本节点只建立单版本活动路由，不能据此宣称灰度、晋级、回滚或控制面故障隔离已经完成。

## 验证指标

- 自定义服务创建只产生一个活动服务、一个访问策略版本、一个初始路由和一个当前指针，幂等重放不增加事实。
- 跨工作空间 Release、不同 Agent Release、失效 Agent、无效主体和不完整访问策略均失败关闭。
- 数据库直接更新或删除访问策略和历史路由必须失败；伪造 Route 或绕过状态机激活服务必须失败。
- 系统运行配置变化后，`agent_publications.release_id` 与系统服务当前 Route 的 `primary_release_id` 一致，旧 Run 仍保留原 Release。
- Migration 空库升级、带既有系统助手数据升级、降级安全检查及阶段 1 助手回归通过。

## 迁移与回滚

- Revision `20260816_0047` 新增服务治理五张表、复合外键、不可变 Trigger、状态迁移 Trigger 和系统助手回填。
- 升级先创建空表与约束，再从当前 `agent_publications` 回填；没有系统助手发布的工作空间不会被创建空服务。
- 若已存在自定义服务、超过一个路由版本或服务更新请求，拒绝降级，避免静默丢失控制面事实。
- 允许降级时只删除服务治理表，不删除 `agent_publications`、AgentRelease、会话或 Run，阶段 1 助手可继续工作。

## 后续事项

- `P3-08` 从当前 Route 装载唯一 Release，并建立控制面故障隔离。
- `P3-09` 基于 `service_route_publications.generation` 实现灰度、晋级、回滚和并发胜者。
- `P3-10` 在服务出口执行版本化访问策略，并保证 API Key Scope 只能收窄权限。
- `P3-11` 激活服务菜单、权限和浏览器管理接口。

## 关联内容

- [阶段 3 实施计划](../stages/stage-3-plan.md)
- [阶段 3 验证报告](../stages/stage-3-report.md)
- [Agent 控制面基线](../../contracts/agent-control/agent-control-baseline.v1.json)
- [ADR-006：工作空间数据生命周期与可校验导出包](./ADR-006-workspace-data-lifecycle.md)
