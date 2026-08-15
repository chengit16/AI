# ADR-005：跨实例 SSE 采用 Valkey Pub/Sub 唤醒与 PostgreSQL 回放

## 状态

`已接受`

## 日期

2026-08-15

## 背景

阶段 1 已使用 PostgreSQL `stream_runs` 和 `stream_events` 保存 Run、严格递增序号、事件、24 小时保留期和最终快照。HTTP SSE 通过 `Last-Event-ID` 回放已提交事件，等待期间每 250 毫秒使用独立短事务查询，不持有数据库连接，也不会因重连重新创建 Run。

阶段 2 需要允许客户端在任一 API 实例中断后连接其他实例，并在 5 秒内继续回放。仅依赖进程内信号无法跨实例，继续固定频率轮询会在连接数增长时产生不必要查询；把事件复制到第二套持久队列又会引入双事实源、序号对账和保留期一致性问题。平台当前已有 Valkey，但它在既有架构中只承担可失效 Session、缓存、Broker 和唤醒状态，不能取得业务事实写入权。

## 决策驱动因素

- PostgreSQL 必须继续作为 Run、事件、序号、游标和最终快照的唯一事实源。
- 通知丢失、乱序或 Valkey 中断不能丢事件、重复生成或结束已建立的 SSE 连接。
- 唤醒消息不得携带问题、回答、引用、受限字段、工作空间信息或供应商凭据。
- 任意 API 实例必须只凭可信 `RequestContext`、Run 标识、`Last-Event-ID` 和 PostgreSQL 恢复。
- 本地默认恢复时限为 5 秒，且不提前建设 SaaS、多活、Go 运行层或新消息中间件。

## 备选方案

### 方案 A：仅使用 PostgreSQL 定时轮询

事实边界最简单，通知故障面为零，现有 250 毫秒轮询也能满足本地恢复时限。但每条空闲 SSE 连接都会持续查询数据库，连接规模增长时形成与事件量无关的读放大，不能提供主动的跨实例唤醒。

### 方案 B：Valkey Pub/Sub 无正文唤醒，加 PostgreSQL 有界轮询兜底

生产者在 PostgreSQL 提交后，仅向 Run 专属频道发布固定载荷；订阅实例收到信号后回查 PostgreSQL。Pub/Sub 不保留消息，订阅或发布失败时客户端继续按配置周期轮询事实库。该方案不需要确认、重放或清理通知消息，数据职责清晰，改动集中在 Streaming Adapter 和 SSE 等待策略。

### 方案 C：使用 Valkey Streams 保存可重放通知

Streams 可以消费确认、重放和保留消息，但平台仍必须回查 PostgreSQL 才能获得授权后的完整事件。它会额外引入 Consumer Group、Pending Entries、裁剪、恢复和双保留期运维，而当前 5 秒恢复目标不要求通知本身持久化。

## 决策

采用方案 B，并固定以下规则：

1. `TransactionalStreamService` 只有在 PostgreSQL 事务提交成功后才发布唤醒。创建 Run、追加事件和写入终态都会通知；通知失败不得改变已提交业务返回值。
2. 唤醒频道使用版本化前缀和不可枚举的 Run UUID，消息载荷固定为 `1`。通知中不出现工作空间、会话、消息、事件 Payload、Trace 或正文。
3. 每条 SSE 连接只订阅目标 Run。收到信号只表示“数据库中可能有新事实”，调用方必须重新执行按可信工作空间隔离的 PostgreSQL 回放，不能直接向客户端转发通知载荷。
4. Pub/Sub 允许丢失、重复和乱序。无通知、订阅失败、连接中断或超时均继续按 `stream_poll_interval_ms` 回查 PostgreSQL；默认 250 毫秒，配置边界为 50～4000 毫秒，为 5 秒恢复门禁保留至少 1 秒查询与传输余量。
5. API 实例之间不需要粘性会话，也不保存进程内游标。客户端重连其他实例时继续携带最后一个持久化 `Last-Event-ID`；`message.snapshot` 不产生新的 SSE 游标。
6. 当前不采用 Valkey Streams。只有有界轮询无法满足经过容量验收的恢复目标，或出现必须独立于 PostgreSQL 保存通知消费进度的明确需求时，才创建新 ADR 重新评估。

## 影响

### 正面影响

- 正常情况下新事件提交后立即唤醒其他 API 实例，减少空闲 SSE 对数据库的无效查询。
- 通知层完全丢失时仍能从 PostgreSQL 恢复，不需要对 Valkey AOF、Pub/Sub 消息或消费者状态做业务备份。
- SSE 授权、事件顺序、回放预算和 24 小时保留逻辑不变，前端协议与 OpenAPI 无破坏性变化。
- Adapter 接口只暴露 `publish(run_id)`、`subscribe(run_id)` 和有界等待，未来替换通知实现不会把 RESP 细节带入领域层。

### 负面影响与风险

- 每条活动 SSE 连接会占用一个 Pub/Sub 连接；正式容量验收必须观察 API 文件描述符、Valkey 连接数和数据库兜底查询量。
- Valkey 故障时系统会退化为固定周期数据库轮询，恢复正确性保持不变，但数据库读取会暂时增加。
- Pub/Sub 不提供送达证明，不能把通知接收数当作事件完整性指标；完整性只能由 PostgreSQL 序号和回放结果判定。
- 当前本地 20 样本验证不等同于 500 条 SSE 的条件容量认证，生产规模结论继续为 `not_run`。

## 验证指标

- 两个独立流服务共享 PostgreSQL 与 Valkey 时，写实例提交后读实例可被唤醒并读取同一事件。
- 写实例中断后，替代实例从原 `Last-Event-ID` 继续回放，不创建新 Run，序号无缺口。
- 跨工作空间 Run 查询失败关闭；通知接口和频道消息不携带正文或工作空间信息。
- 丢失全部通知或 Valkey 端口不可达时，事件提交和 PostgreSQL 回放继续成功。
- 本地至少 20 个跨实例恢复样本按 nearest-rank 计算 `p99 <= 5s`；500 条 SSE 容量认证保持 `not_run`。
- `./scripts/verify`、专项 PostgreSQL/Valkey 集成测试和 `./platform doctor` 通过。

## 迁移与回滚

本决策不修改数据库 Schema、SSE V1 契约或前端游标。升级后 API 进程新增 Valkey Pub/Sub 客户端，并保持原轮询参数作为兜底。若通知实现导致连接泄漏、资源异常或回归，可移除通知 Adapter 装配并回到纯 PostgreSQL 轮询；Run、事件、序号和客户端游标无需迁移或回滚。

## 后续事项

- `P2-06` 增加通知发布失败、订阅降级、唤醒到回放延迟、活动订阅数和兜底查询量指标，日志继续禁止记录频道正文以外的信息。
- `P2-11` 使用实际双 API 进程完成实例停止、Valkey 中断和客户端重连联合演练。
- `CAP-*` 在合适机器上执行 500 条 SSE 容量认证，再决定是否需要连接复用、轮询退避或重新评估 Valkey Streams。

## 关联内容

- [ADR-001：阶段 1A 使用 Valkey 替换 Redis 7.4](./ADR-001-replace-redis-with-valkey.md)
- [阶段 2 实施计划](../stages/stage-2-plan.md)
- [阶段 2 验证报告](../stages/stage-2-report.md)
- [后端代码规范](../governance/backend-code-standards.md)
