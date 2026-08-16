# ADR-010：统一服务调用出口与双身份执行边界

## 状态

已接受

## 日期

2026-08-16

## 背景

`P3-07`～`P3-09` 已建立不可变 Service、访问策略、版本化 Route、Runtime 隔离、稳定灰度和追加式
回滚，但这些能力仍只有内部用例，不能被自定义知识 Agent、场景应用或 Open API 安全调用。
三类出口都需要创建 Assistant Run、执行 RAG、消费月度配额并提供 HTTP/SSE 恢复；若各自维护
调用链，访问策略、路由冻结、限流、幂等和事件恢复会产生不同安全语义。

Open API Key 同时具有两个不能混淆的身份：API Key Actor 是一次调用的凭证主体，创建账号则是
服务访问策略和知识权限所属的人类账号。把两者合并会让多个 Key 共享幂等与读取边界；直接使用
Key 的服务级 Scope 执行内部 RAG，又会把“允许调用某个 Service”错误扩大为“允许读取该 Service
引用的全部知识”。

## 决策驱动因素

- 三类出口必须复用同一 Service 当前 Route 和访问策略，不能旁路执行草稿或可变配置。
- Open API Key Scope 只能收窄账号既有权限，并支持精确限制到一个 Service。
- 不同 API Key 即使由同一账号创建，也必须隔离幂等、运行读取、SSE 恢复和审计归属。
- 内部 RAG 必须按账号重新执行 RBAC/ABAC，不能继承服务级 Scope 或入口授权结论。
- HTTP 快照与 SSE 必须观察同一 Run，断点恢复不得重新调用模型或重复消费配额。
- 限流、配额、访问策略或 Runtime 事实不可用时必须默认拒绝，不能产生部分 Run。
- 当前阶段不得引入 Channel Gateway、外部写操作、真实多源连接器或 Durable Run。

## 备选方案

### 方案 A：三类出口分别实现调用链

自定义知识 Agent 复用普通问答，场景应用维护独立会话，Open API 再增加专用执行器。短期改动
分散，但三条链会分别解释访问策略、限流、配额、路由和 SSE；相同 Service 在不同 surface 上
可能选择不同 Release 或产生不同越权结果，因此拒绝。

### 方案 B：所有调用都归属创建账号

浏览器和 API Key 都把账号 ID 作为 Run 所有者，内部 RAG 可以直接复用账号权限。实现较简单，
但同一账号创建的多个 Key 会共享幂等键与 Run 读取能力，撤销一个 Key 后也无法从历史审计判断
实际凭证主体，因此拒绝。

### 方案 C：统一出口，Actor 归属调用，账号承担业务权限

三类 surface 统一进入 ServiceInvocation 用例。浏览器 Actor 等于账号；API Key 使用独立 Actor
拥有幂等、读取、SSE 和审计边界，创建账号只在服务访问策略与内部 RAG 中承担业务权限。内部
检索清除 Key Scope 和入口授权结论后重新执行账号 RBAC/ABAC。采用此方案。

## 决策

- 新增独立 `service_delivery` 模块，统一编排服务入口授权、固定窗口限流、Runtime 当前 Route、
  访问策略复核、月度问答配额、隐藏会话、Assistant Run 和响应投影。该模块不拥有 Service、
  Route、Release、权益或 SSE 事件事实，只通过既有端口组合它们。
- `custom_knowledge_agent` 和 `scenario_application` 只接受浏览器身份；`open_api` 只接受 Open API
  Key。surface 错配、服务暂停、访问策略拒绝或 Runtime 漂移均在提交 Run 前失败关闭。
- 服务调用接口统一绑定 `service.definition.read`。API Key 可持有权限级 Scope，也可使用严格的
  `service.definition.read.{service_id_hex}` 服务级 Scope；资源后缀必须是 32 位小写 hex，其他
  权限前缀不得伪装成服务级 Scope。
- `requested_by_actor_id` 是 Run 幂等、读取、SSE 恢复和审计归属；`requested_by_account_id` 是
  人类账号。浏览器请求要求两者相等，API Key 请求要求 Actor 独立且账号仍为当前有效成员。
- 服务调用创建 `conversation_kind=service_invocation` 的隐藏会话，不进入普通私有会话列表。
  POST、GET 快照和 SSE 都按工作空间、Service、Actor 与 Run 复核，不能跨 Actor 读取。
- API Key Run 进入检索前恢复创建账号执行上下文，清除 `credential_scopes`、入口 Permission、
  数据范围和字段授权，再由检索链重新执行账号 RBAC/ABAC。运行归属和最终审计仍保留原 API
  Key Actor，服务级 Scope 不能替代文档或知识库权限。
- 月度问答用量、隐藏会话、消息、Run、审计和 Outbox 在同一数据库事务提交。配额拒绝不能留下
  会话或 Run；相同 Actor 与幂等键重放返回原结果，不重复消费用量。
- Valkey 使用 Lua 原子固定窗口，键只包含工作空间、Service、Actor 和幂等键的 SHA-256 摘要。
  幂等重放免重复计数；超过窗口返回 `SERVICE_RATE_LIMITED`，Valkey 不可用或返回不可验证结果
  时返回 `SERVICE_RATE_LIMIT_UNAVAILABLE` 并默认拒绝。
- SSE 继续以 PostgreSQL 为唯一事件事实，复用 Valkey 无正文唤醒和有界轮询。`Last-Event-ID`
  只恢复同一 Run 的事件游标，不创建新 Run、不重新执行模型、不重新消费配额。

## 影响

### 正面影响

- 三类 surface 对同一 Service 使用完全一致的 Route、Release、访问策略、配额和恢复语义。
- API Key Actor 可独立撤销、追溯和隔离，多个 Key 不会因共享创建账号而互相读取 Run。
- 内部 RAG 继续由账号 RBAC/ABAC 保护，服务级 Scope 只决定能否调用 Service，不扩大知识权限。
- HTTP 与 SSE 共用既有 Assistant/Streaming 实现，断点恢复和 PostgreSQL 事件事实保持单一来源。

### 负面影响与风险

- Assistant Run 同时保存账号和 Actor，两者语义不同；后续查询必须明确使用哪一身份，不能默认
  两者相等。
- 每次服务调用创建隐藏会话，会增加会话与消息事实数量；P3-12 需要按 Service/Release 聚合，
  不能把隐藏会话混入普通用户会话指标。
- 限流依赖 Valkey 且失败关闭，Valkey 故障期间新调用会被拒绝；这优先保证配额和滥用边界，
  不提供绕过限流的降级路径。
- 当前固定窗口是单一默认平台配置，不包含套餐级或 Service 级自定义限流；若后续开放配置，必须
  继续保持服务端上限和低基数观测。

## 验证指标

- 三类 surface 均能创建绑定当前 Route/Release 的 Run，surface 错配和跨空间调用零副作用拒绝。
- 服务级 Scope 只覆盖指定 Service；非 32 位、非小写 hex 和错误权限前缀不能签发。
- 同一账号的两个 API Key 使用相同幂等键产生不同 Run，且不能互相读取 HTTP/SSE 结果。
- API Key 内部 RAG 重新执行账号 RBAC/ABAC，越权知识不进入检索、模型上下文或响应。
- 月度配额拒绝不产生 Run；固定窗口超过阈值返回 429，幂等重放不重复计数。
- POST、GET、完整 SSE 和 `Last-Event-ID` 断点回放指向同一 Run，执行器只调度一次。
- Migration 空库与含历史角色、菜单、会话的非空库升级、降级和再升级通过；Actor 改绑由数据库
  拒绝，存在服务调用事实时不安全降级失败。
- 统一门禁、真实 PostgreSQL/Valkey 集成、容器重建和 13 项平台诊断通过。

## 迁移与回滚

- Revision `20260816_0050` 为会话增加类型，为 Run 增加 Actor 归属，并把历史普通会话和 Run
  回填为账号 Actor；既有角色获得服务读取权限，当前菜单发布快照升级到 Registry 19。
- 应先升级数据库，再部署包含服务出口的 API。升级后旧应用仍可读取新增可空兼容字段，但不会
  暴露三类服务调用接口。
- 回滚应用前应停止新服务调用；只有不存在 `service_invocation` 会话和独立 Actor 运行事实时，
  才允许数据库降级到 `0049`。Migration 会拒绝丢失调用身份的降级。
- Valkey 限流键是可重建派生数据，回滚时可清除 `service-invocation-rate:v1` 和
  `service-invocation-replay:v1`；PostgreSQL Run、消息、审计、用量和 SSE 事件不得删除。

## 后续事项

- `P3-11` 在 Agent 控制台中消费统一出口，页面、动作和三个 Operation 继续进入自定义菜单与
  接口统一授权，不另建前端调用协议。
- `P3-12` 按 Service、Route 和 AgentRelease 聚合质量、延迟、错误与成本，并区分普通会话和
  ServiceInvocation；不暴露 Actor、Prompt 或正文等高基数敏感信息。
- 阶段 4 如引入写工具，必须新增确认、幂等、取消和工具权限状态机；不得复用本 ADR 的只读服务
  Scope 直接授权外部写操作。

## 关联内容

- [阶段 3 实施计划](../stages/stage-3-plan.md)
- [阶段 3 验证报告](../stages/stage-3-report.md)
- [ADR-008：Runtime 发布装载与控制面故障隔离](./ADR-008-runtime-release-loading-and-control-plane-isolation.md)
- [ADR-009：稳定灰度、原子晋级与追加式回滚](./ADR-009-stable-canary-promotion-and-rollback.md)
- [AI 智能平台领域语言](../../CONTEXT.md)
