# 平台共享契约

## 1. 目录职责

| 目录             | 内容                                                         |
| ---------------- | ------------------------------------------------------------ |
| `openapi/`       | OpenAPI 3.1 HTTP 接口契约                                    |
| `domain/`        | 核心标识、消息和后置扩展接口的数据契约                       |
| `policy/`        | 跨进程策略请求与决策结果契约                                 |
| `authorization/` | Permission、页面、接口、系统菜单和字段敏感级别的版本化注册表 |
| `sse/`           | SSE 消息事件信封契约                                         |
| `events/`        | Transactional Outbox 集成事件与内部签名任务信封契约          |
| `errors/`        | 稳定错误码目录                                               |
| `release/`       | `ReleaseManifest`、兼容矩阵及运行组合规则                    |
| `reliability/`   | 可靠性不变量、SLO、保留期、传播时限和故障场景契约            |
| `agent-control/` | Agent 草稿、发布快照、服务路由、运行绑定和阶段安全基线       |
| `tool-execution/` | 工具定义、任务状态、确认、幂等、取消和阶段安全基线          |
| `quality/`       | 质量样本来源、授权投影、摘要、版本和删除传播契约             |
| `fixtures/`      | Python 和未来 Go 实现共用的 Golden Fixtures                  |

## 2. 版本规则

- 文件名包含主版本，例如 `message-event.v1.schema.json`。
- `schema_version` 是正整数，当前固定为 `1`。
- 同一主版本只允许新增可选字段或新增事件类型；消费者必须忽略未知可选字段。
- 删除字段、字段改名、改变字段语义或收紧既有输入时发布新主版本。
- `workspace_id`、`actor_id`、`event_id`、`sequence_no` 和 `permission_code` 不得由实现自行换名。

## 3. 安全边界

- 外部客户端传入的 `actor_id`、角色和策略版本不可信，必须由完成身份验证的服务端重新构造。
- `workspace_id` 是数据隔离边界，不得使用空值表示平台管理员绕过隔离。
- 策略中心失败、超时或响应不符合 Schema 时默认拒绝。
- `field_mask` 和 `resource_scope` 由数据责任模块执行，代理层不能自行降低约束。
- SSE 和集成事件的 `event_id` 用于幂等，业务顺序分别由 `sequence_no` 和 `aggregate_version` 表达。
- 内部任务信封必须在生产者签名、消费者验签后才能恢复可信主体和 Trace；Broker 中的普通载荷不构成身份事实。
- Agent Runtime 只能装载不可变 `AgentRelease`；草稿、候选、测试结果或当前配置不能通过旁路成为运行输入。
- 工具 Runtime 只能执行 Release 允许列表与当前策略共同允许的不可变工具版本；模型输出、客户端参数和菜单可见性均不能扩大权限。

## 4. 验证

```bash
uv run pytest tests/contract
```

测试会校验全部 JSON Schema、Golden Fixtures、错误码唯一性、OpenAPI 3.1 基线以及 FastAPI 实现的路由和响应字段。

`ReleaseManifest` 由构建系统提供镜像与源码摘要后生成，生成器不会从可变标签推断不可变摘要。仓库内清单 Fixture 只使用合成摘要验证契约和兼容逻辑，不代表可发布镜像。

节点最终验收通过 `./scripts/verify` 执行完整 OpenAPI 漂移和相对 Git 基线的同主版本兼容检查。未来 CI 使用 `AI_PLATFORM_CONTRACT_BASE_REF` 指向目标分支 Merge Base；破坏性变更必须发布新主版本并提供迁移与回滚方案。
