# 工具执行契约

## 1. 目录职责

本目录固定阶段 4 的受控工具执行语义。`tool-execution.v1.schema.json` 定义不可变工具版本、`Run`、`Step`、`Attempt`、`ToolCall`、策略决策、确认/审批、幂等事实、取消事实和安全结果；`tool-execution-baseline.v1.json` 冻结状态机、安全不变量、首批五个内部只读工具以及后续实现使用的权限、菜单、API 和事件标识；场景 Schema 约束版本化全合成验收数据。

预留标识不是已上线能力。只有对应节点完成实现、OpenAPI、资源注册表、菜单发布、Migration 和验收后，权限或接口才能进入运行平台。`P4-01` 不创建活动数据库表、API、菜单、凭证或真实 Adapter；`P4-02` 只落地不可变工具注册和工作空间目录服务；`P4-03` 已落地内部 Run/Step/Attempt/ToolCall 状态事实、租约和唯一写入权；`P4-04` 已落地五个冻结内部只读工具的统一 Adapter、Schema、资源范围和结果安全边界，但仍未激活工具 HTTP API、菜单、模型工具计划、凭证值或真实外部连接器。

## 2. 核心边界

- Runtime 只执行不可变 `AgentRelease` 允许列表中的不可变工具版本，并在每一步按可信 `RequestContext` 重新执行当前策略。
- 模型只产生结构化候选意图；参数校验、工具解析、策略、确认、凭证注入、调用和结果安全处理均由确定性 Runtime 完成。
- 有副作用的调用在执行前必须同时具备有效确认/审批和稳定幂等身份；确认绑定规范参数摘要、策略版本、Run、Step 与工具版本。
- 个人空间由所有者确认，企业空间复用既有多级审批；拒绝、过期、撤回、撤权或参数变化均使旧确认失效。
- 取消、超时或租约失效后不再创建新 Attempt；迟到结果只保留为审计证据，不能覆盖已经提交的终态。
- 凭证只通过 `credential_ref` 在 Adapter 调用边缘短时注入，不能进入 Prompt、模型上下文、事件、日志、审计正文、SSE、异常或 API 响应。
- 工具结果按不可信输入处理，经过结构、大小、敏感字段和 Prompt Injection 检查后才能进入后续模型上下文。
- 首批只开放五个内部只读工具；阶段 4 的写入验证只使用合成内部 Adapter，不允许任意 HTTP、SQL、文件系统或真实外部系统调用。

## 3. 版本规则

同一主版本可以增加可选字段、新的只读工具版本、新事件类型或新终端读模型，不能删除既有字段、放宽确认/审批和幂等门禁、允许模型直接调用 Adapter、允许迟到结果覆盖终态或扩大凭证暴露范围。状态转换、工具执行写入权、确认摘要、幂等提交协议或取消优先级发生变化时，必须先形成新的 ADR；破坏性协议变化发布新主版本并提供迁移和回滚方案。

## 4. 验证

```bash
uv run --locked pytest tests/test_p401_tool_execution_contracts.py tests/contract/test_contracts.py
uv run --locked python scripts/check_contract_compatibility.py HEAD
./scripts/verify
```

`P4-02` 另以 `tests/unit/test_p402_tool_catalog.py` 和 `tests/integration/test_p402_tool_catalog_postgres.py` 验证定义摘要、Schema、套餐与权限交集、精确历史版本校验、数据库不可变约束和 Migration 往返。

`P4-03` 另以 `tests/unit/test_p403_tool_task_state.py` 和 `tests/integration/test_p403_tool_task_state_postgres.py` 验证可信身份、预算和摘要收敛、Run/Step/Attempt/ToolCall 状态转换、`FOR UPDATE SKIP LOCKED` 并发领取、租约代际、取消优先级、迟到结果、跨空间隔离、终态保护和 Migration 往返。

`P4-04` 另以 `tests/unit/test_p404_internal_read_adapters.py` 和 `tests/integration/test_p404_internal_read_adapters_postgres.py` 验证五工具分发、真实目录精确版本、当前套餐/PDP、输入输出 Schema、空资源范围、字段遮罩、结果大小、敏感字段、Prompt Injection 和 Adapter 故障的失败关闭行为。
