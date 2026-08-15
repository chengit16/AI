# 可靠性契约

## 1. 目录职责

本目录固定阶段 2 的可靠性语义，不保存某次测试结果。`reliability-baseline.v1.json` 定义可靠性不变量、SLO、事实来源、测量窗口、保留期和权限传播时限；`failure-scenarios.v1.schema.json` 约束可重复的全合成故障注入数据。

## 2. 测量规则

- 延迟类门禁使用尾部百分位或最大值，不以平均值掩盖长尾失败。
- `p99` 至少需要 20 个有效样本；样本不足时状态必须保持 `not_run`，不能判定通过。
- 失败和超时样本必须进入分母或延迟样本，不能在计算前剔除。
- PostgreSQL 业务事实、不可变审计、Outbox、任务 Attempt、SSE 事件和 Trace 是证据来源；日志文字本身不能替代业务事实。
- 可靠性 Fixture 只使用合成标识、合成内容和可控故障，不连接真实客户或模型供应商。

## 3. 变更规则

同一主版本可以新增可选指标或场景，不能静默放宽阈值、缩短保留期、删除失败场景或改变失败关闭语义。需要放宽门禁时必须新增 ADR，说明风险、迁移和回滚方案。

## 4. 验证

```bash
uv run --locked pytest tests/test_p201_reliability_baseline.py tests/contract/test_contracts.py
```
