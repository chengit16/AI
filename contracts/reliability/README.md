# 可靠性契约

## 1. 目录职责

本目录固定阶段 2 的可靠性语义，不保存某次测试结果。`reliability-baseline.v1.json` 定义可靠性不变量、SLO、事实来源、测量窗口、保留期和权限传播时限；`failure-scenarios.v1.schema.json` 约束可重复的全合成故障注入数据；`stage-2-drill.v1.schema.json` 与 `stage-2-drill-evidence.v1.schema.json` 分别约束 P2-11 联合演练清单和本地结果证据。

## 2. 测量规则

- 延迟类门禁使用尾部百分位或最大值，不以平均值掩盖长尾失败。
- `p99` 至少需要 20 个有效样本；样本不足时状态必须保持 `not_run`，不能判定通过。
- 失败和超时样本必须进入分母或延迟样本，不能在计算前剔除。
- PostgreSQL 业务事实、不可变审计、Outbox、任务 Attempt、SSE 事件和 Trace 是证据来源；日志文字本身不能替代业务事实。
- 可靠性 Fixture 只使用合成标识、合成内容和可控故障，不连接真实客户或模型供应商。

## 3. 联合演练证据

- `p2-11-v1` 只登记受 Schema 限制且能在仓库 AST 中定位的 pytest 节点，不从 JSON 执行任意 Shell 命令。
- `./platform drill-stage-2` 在场景前后分别执行 13 项平台诊断；前置诊断失败时不运行场景，恢复诊断无论中间结果如何都必须执行。
- JUnit 只用于关联固定场景状态；写入 `.ai-platform/evidence/p2-11-latest.json` 的证据仅包含版本、Git Revision、工作区状态、时间、耗时、稳定原因码和计数，不保存 stdout、stderr、异常正文、业务载荷或凭据。
- 任一场景未通过、未执行、JUnit 无效或恢复后诊断失败时，整次演练必须返回失败；本地小规模通过不替代 Linux、真实供应商或条件容量认证。

## 4. 变更规则

同一主版本可以新增可选指标或场景，不能静默放宽阈值、缩短保留期、删除失败场景或改变失败关闭语义。需要放宽门禁时必须新增 ADR，说明风险、迁移和回滚方案。

## 5. 验证

```bash
uv run --locked pytest tests/test_p201_reliability_baseline.py tests/test_p211_reliability_drill.py tests/contract/test_contracts.py
./platform drill-stage-2
```
