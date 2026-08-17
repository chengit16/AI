# 工具执行契约

## 1. 目录职责

本目录固定阶段 4 的受控工具执行语义。`tool-execution.v1.schema.json` 定义不可变工具版本、`Run`、`Step`、`Attempt`、`ToolCall`、策略决策、确认/审批、幂等事实、取消事实和安全结果；`tool-execution-baseline.v1.json` 冻结状态机、安全不变量、首批五个内部只读工具以及后续实现使用的权限、菜单、API 和事件标识；场景 Schema 约束版本化全合成验收数据；`stage-4-tool-drill*.schema.json` 约束 `P4-12` 固定联合故障清单与不含业务正文的本地最小证据。

预留标识不是已上线能力。只有对应节点完成实现、OpenAPI、资源注册表、菜单发布、Migration 和验收后，权限或接口才能进入运行平台。`P4-01` 冻结契约与边界；`P4-02`～`P4-05` 已落地不可变工具注册、工作空间目录、Run/Step/Attempt/ToolCall 状态事实、五个内部只读 Adapter、严格候选意图、Release 允许列表、预算、当前 PDP 证据和原子计划冻结；`P4-06`～`P4-10` 已落地个人确认、企业审批、凭证边缘注入、合成副作用幂等、Worker 恢复、安全结果与连续进度事实。

`P4-11` 已正式激活工具目录、任务控制、确认/驳回/取消、可恢复 SSE、菜单、浏览器 API 和页面；Runtime 与 Worker 内部执行入口仍不注册为浏览器 API。阶段 4 仍只开放五个内部只读工具，写操作只由全合成内部 Adapter 验证控制机制，不包含真实外部连接器、客户凭证或任意 HTTP、SQL、文件系统工具。

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

`P4-05` 另以 `tests/unit/test_p405_tool_planning.py` 和 `tests/integration/test_p405_tool_planning_postgres.py` 验证严格候选信封、Release 快照允许列表、当前套餐/PDP、参数 Schema、Step 预算、策略证据、单事务冻结、数据库防绕过、冲突回滚和 Migration 往返。

`P4-12` 使用 `./platform accept-stage-4-tools` 在前后各执行一次 `./platform doctor`，并以一次 pytest/JUnit 运行固定 8 类真实 PostgreSQL 故障节点；Shell 包装层由回归测试把整个函数体精确冻结为 6 条非空行，只能依次执行 `check_runtime`、`ensure_environment`、`export_local_service_settings` 和唯一正式执行器及其清单、证据参数，不能追加其他命令或扩大执行范围。清单的前置/恢复命令、前置失败停止、始终恢复诊断、失败关闭模式和证据路径还分别具有参数化反例，任一放宽都会拒绝。清单同时要求完整节点和裸测试函数名各自唯一，将每类能力与场景 ID、P4-01 来源和 PostgreSQL 节点三元绑定，并在清单加载阶段精确绑定每个能力的样本类别与验证不变量；即使全局总并集仍完整，单场景虚增样本类别或减少自身验证责任也会失败关闭。清单冻结 8 个场景对应 9 个 JUnit case；凭证场景除数量外还精确要求 `[rotate]` 和 `[revoke]` 两个参数 ID，缺失与同数量身份替换分别记录 `junit_case_count_mismatch` 和 `junit_case_identity_mismatch`，冻结 case 全部存在时若仍混入额外 testcase 则整份 JUnit 失败关闭。正式证据只能由仓库冻结清单生成，保证固定 `manifest_ref` 与校验时冻结的 SHA-256 一致，并在清单校验后、恢复诊断后和通过证据原子写入后复核清单未发生漂移；最后一次复核失败会删除刚写出的证据，未知 Git 修订也不能判定通过。确认使用冻结清单后，执行器先拒绝与清单、Schema、基线、来源场景或真实测试节点重合的证据路径，并要求所有证据目标都位于当前 `AI_PLATFORM_ROOT/evidence`；仓库外任意文件同样不能成为失效或覆盖目标。通过路径守卫的新尝试才会使旧证据失效，避免清单校验或命令异常继续暴露上次通过结论，替代清单也不能触发该失效操作。前置、场景或恢复 Runner 抛出的未捕获异常统一收敛为 `runner_exception`，前置或场景异常仍会执行恢复诊断；Runner 返回越界退出码、非有限耗时、成功携带原因或未注册原因时统一记为 `runner_invalid_result`。证据 Schema 只接受冻结原因码枚举，证据写入 `.ai-platform/evidence/p4-12-latest.json`；每个场景记录 `expected_case_count` 与 `observed_case_count`，汇总固定 `expected_case_total = 9` 并记录 `observed_case_total`。Schema 以八组 `contains/minContains/maxContains` 独立固定每个场景的 ID、能力、样本类别、真实测试节点和期望 case 数，重复或替换任一绑定都会被拒绝；六类样本汇总同步固定为成功 `1`、失败 `5`、超时 `2`、取消 `1`、恢复 `3`、拒绝 `3`。`overall_status = passed` 时还要求三个检查和八个场景全部通过、期望与实际 case 都为 `9`，并且失败、错误、跳过和未运行计数全部为 `0`。证据其余字段只包含清单摘要、场景与样本类别、测试节点、状态、耗时和稳定原因码，不包含参数正文、结果正文、凭证或测试错误正文。

收口时进一步把每个场景的 `verified_invariants` 写入最小证据，并由八组 Schema 约束精确绑定；证据消费者无需只依赖样本类别或测试节点推断验证责任，不变量归属被减少或替换时会直接校验失败。
