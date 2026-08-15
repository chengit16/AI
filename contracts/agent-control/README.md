# Agent 控制面契约

## 1. 目录职责

本目录固定阶段 3 的 Agent 控制面和服务发布语义。`agent-control.v1.schema.json` 定义 `Agent`、`AgentDraft`、发布候选、不可变 `AgentRelease`、`Service`、版本化 `ServiceRoute` 和 Run 发布绑定；`agent-control-baseline.v1.json` 冻结状态机、安全不变量以及后续实现使用的权限、菜单、API 和事件标识；场景 Schema 约束全合成验收数据。

预留标识不是已上线能力。只有对应节点完成实现、OpenAPI、资源注册表、菜单发布、Migration 和验收后，权限或接口才能进入运行平台。

## 2. 核心边界

- Runtime 只接收 `AgentRelease`，不能执行 `AgentDraft` 或发布候选。
- `AgentRelease` 内容不可变；灰度、晋级和回滚只追加 `ServiceRoute` 版本。
- 每个 Run 固定唯一服务、路由版本和发布版本，在途运行不随当前路由切换。
- 发布快照必须绑定配置、固定测试集、必需安全测试、审批实例、审批策略版本和稳定摘要。
- 个人空间使用所有者审批，企业空间复用既有多级审批；菜单可见性和接口授权均由统一资源模型控制。
- 阶段 3 只允许已登记的只读工具，不包含外部写操作、LLM Grading、多模态图片问答或其他后置能力。

## 3. 版本规则

同一主版本可以增加可选字段、状态以外的新读模型或新事件类型，不能删除既有字段、放宽必需安全门禁、允许草稿执行或改变发布不可变语义。状态转换、数据写入权、路由一致性或故障隔离发生变化时必须先形成 ADR。

## 4. 验证

```bash
uv run --locked pytest tests/test_p301_agent_control_contracts.py tests/contract/test_contracts.py
uv run --locked python scripts/check_contract_compatibility.py HEAD
```
