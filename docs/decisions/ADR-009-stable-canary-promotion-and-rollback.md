# ADR-009：稳定灰度、原子晋级与追加式回滚

## 状态

已接受

## 日期

2026-08-16

## 背景

`P3-07` 已建立稳定 `Service`、不可变 `ServiceRoute` 与带 `generation` 的当前路由指针，
`P3-08` 已让 Runtime 只从发布事实装载单版本 `active` Route，并为新 Run 冻结服务、路由和
`AgentRelease` 绑定。平台仍不能把新 Release 按稳定比例灰度给调用方，也没有原子晋级和一键
回滚用例。现有 Runtime current 缓存以服务为单一键，既不能区分灰度分配结果，也不能在发布
切换后一次清除全部灰度桶。

灰度、晋级和回滚不能修改 `AgentRelease` 或历史 Route，也不能把当前指针直接移回旧 Route。
两个发布者基于相同 `generation` 并发操作时必须只有一个胜者；失败请求不能留下 Route、服务
版本、幂等请求、审计或 Outbox 等部分副作用。

## 决策驱动因素

- 同一服务和分配键重复解析必须得到相同分桶，提高灰度比例时既有低位桶不能重新洗牌。
- 分配键可能来自账号、会话或 API 调用方，原始值不得进入持久化事实、缓存键、日志或审计。
- 当前 Route 切换只影响新 Run；在途和历史 Run 继续按 `P3-08` 冻结绑定执行。
- 灰度、晋级和回滚必须只追加 Route，并在同一事务切换当前指针、推进服务版本、保存幂等结果、
  写入审计和 Outbox。
- 数据库必须拒绝跨空间、跨 Agent、失效 Release、断裂版本链、跳跃 generation 和非法 Run
  绑定，不能只依赖应用校验。
- 发布提交后应主动清除 Runtime current 缓存；缓存故障不能撤销数据库发布事实，但必须可观测。

## 备选方案

### 方案 A：随机抽样或每次请求重新生成灰度结果

实现简单，但同一调用方会在主版本和灰度版本之间漂移，无法稳定复现问题，也不能证明重复请求
使用同一 Release，因此拒绝。

### 方案 B：使用 `route_id + assignment_key` 计算分桶

每个 Route 内结果稳定，但调整灰度比例会追加新 Route 并改变 `route_id`，导致全部调用方重新
分桶。扩大灰度时无法保持原有灰度群体，故障对比和回滚观察也更困难，因此拒绝。

### 方案 C：使用 `service_id + assignment_key` 计算固定百分位桶

对规范化分配键计算 SHA-256，并映射到 `0..99`。Route 只按 `canary_percent` 判断低位桶是否
选择灰度 Release；提高比例时原有灰度桶保持不变。Runtime current 缓存按百分位桶保存，单个
服务最多 100 个 current 键，可在路由提交后确定性删除。采用此方案。

## 决策

- 分配键执行首尾空白清理和长度、控制字符校验，允许账号、会话或调用方提供稳定的非敏感业务
  标识。计算输入为 `service_id` 二进制、分隔字节和 UTF-8 分配键；SHA-256 前 8 字节转换为
  无符号整数后对 100 取模。系统不保存原始分配键。
- `canary` Route 保持当前稳定 `primary_release_id`，新增不同的 `canary_release_id` 和
  `1..99` 比例；`active` 晋级 Route 把当前灰度 Release 变为唯一 primary；`rollback` Route
  把被回滚 Route 的上一有效 Release 变为唯一 primary。三类操作均让 `previous_route_id`
  指向操作前的当前 Route。
- 回滚当前 `canary` Route 时选择其稳定 primary；回滚已晋级的 `active` Route 时，从紧邻的
  前序 Route 恢复晋级前的稳定 primary。回滚总是创建新的 `rollback` Route，禁止直接重用
  历史 Route 身份。
- 服务治理用例先锁定 Service 和当前 publication，再校验调用方提交的 `expected_generation`。
  Route、publication、Service 版本、幂等请求、审计和 Outbox 在同一事务提交。数据库唯一约束
  或 generation 竞争统一映射为 `SERVICE_ROUTE_CONFLICT`。
- Runtime Source 在单条发布事实查询中按分桶选择 primary 或 canary Release；Loader 同时复核
  Route 结构、摘要和选中 Release。current 缓存按 `workspace_id + service_id + bucket` 保存，
  不可变 bound 缓存继续按 Route 与 Release 精确保存。
- 发布和服务状态变更在事务提交后删除该服务全部 100 个 current 桶及旧版单键缓存。删除失败
  记录结构化告警但不回滚数据库事实；后续请求仍优先读取 PostgreSQL，最多只在发布 Source
  同时故障时暴露旧缓存租期风险。
- Run 数据库 Trigger 接受当前 `canary` Route 中的 primary 或 canary Release，以及当前
  `active`、`rollback` Route 的唯一 primary；仍拒绝历史 Route 新写入、跨空间、失效 Release、
  运行配置错配和任何已落库绑定修改。

## 影响

### 正面影响

- 灰度群体跨比例调整保持稳定，重复请求可通过固定算法和 Route 版本复算。
- Route 历史形成完整追加链，晋级与回滚不会改写 Release 或历史运行证据。
- 固定 100 桶避免按原始主体生成高基数缓存键，也允许发布后执行确定性主动失效。
- generation 与数据库事务共同保证并发发布只有一个当前指针和一组成功副作用。

### 负面影响与风险

- Runtime current 缓存最多从每服务 1 个键增加到 100 个键；默认 5 分钟租期且只在实际访问桶
  写入，容量仍有界。
- PostgreSQL 发布事实不可用且主动失效同时失败时，Runtime 可能在 current 缓存剩余租期内观察
  旧 Route；告警和后续重试必须保留，不能把该情形描述为零陈旧。
- 本节点只建立内部路由用例与 Runtime 选择，不提前开放 HTTP 服务出口或控制台页面；它们分别
  由 `P3-10`、`P3-11` 绑定权限、菜单和访问策略。

## 验证指标

- 固定服务和分配键重复解析 100 次 Release 不漂移；提高灰度比例时已命中的低位桶仍命中。
- 灰度、晋级和回滚每次只新增一条 Route，历史 Route 与 Release 摘要保持不变。
- 两个发布者使用同一 `expected_generation` 并发晋级时只有一个成功，失败方返回
  `SERVICE_ROUTE_CONFLICT`，Route、幂等请求、审计和 Outbox 均无重复副作用。
- 发布后新 Run 观察新 Route，在途 Run 仍能按旧 Route 精确装载并完成。
- current 缓存主动失效覆盖全部 100 个桶；缓存故障有告警且不改变已提交 publication。
- 单元、真实 PostgreSQL、Migration 往返、并发、Runtime/助手回归、统一门禁和容器诊断通过。

## 迁移与回滚

- Revision `20260816_0049` 扩展服务控制幂等操作集合，并替换 Run 绑定 Trigger 以接受合法灰度
  和 rollback Route。该 Migration 不重写历史 Route、Release 或 Run。
- 应先升级数据库，再部署支持灰度 Route 的应用。旧应用会把灰度 Route 视为不可执行，因此不
  支持新旧 API 长时间混跑；升级窗口内不得启动灰度。
- 降级前必须确认当前没有 `canary` 或 `rollback` Route，且不存在绑定这两类 Route 的 Run；
  否则 Migration 拒绝降级，避免旧 Trigger 无法解释现有发布事实。
- Valkey current 键为派生数据，升级或回滚时可以删除 `runtime-current:v1` 与
  `runtime-current:v2` 键并从 PostgreSQL 重新预热；bound 键不因本节点迁移而失效。

## 后续事项

- `P3-10` 为页面、场景和 Open API 服务出口选择稳定、非敏感的 assignment key，并执行访问
  策略和 API Key Scope 收窄后再调用 Runtime。
- `P3-11` 暴露灰度、晋级和回滚页面，所有操作进入自定义菜单与接口统一授权。
- `P3-12` 使用 `service_id + route_version + agent_release_id` 比较主版本和灰度版本指标；自动
  阻断只能依据冻结阈值和确定性指标，不引入 LLM Grading。

## 关联内容

- [阶段 3 实施计划](../stages/stage-3-plan.md)
- [阶段 3 验证报告](../stages/stage-3-report.md)
- [ADR-008：Runtime 发布装载与控制面故障隔离](./ADR-008-runtime-release-loading-and-control-plane-isolation.md)
- [Agent 控制面基线](../../contracts/agent-control/agent-control-baseline.v1.json)
