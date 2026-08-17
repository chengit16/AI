# 质量样本契约

## 1. 目录职责

本目录冻结阶段 5 质量样本闭环的来源类型、授权投影、正文摘要、版本与删除传播约束。契约只描述可进入质量数据集的输入边界和机制证据，不给出真实模型质量结论。

`quality-sample-baseline.v1.json` 是全合成固定基线，正文只用于验证摘要与字段投影，不得被持久化到质量表、审计、Outbox 或运行日志。`quality-sample-baseline.v1.schema.json` 约束基线结构、来源必需字段和显式外部证据状态。

## 2. 稳定规则

- 来源类型固定为 `run_failure`、`user_feedback` 和 `human_correction`。
- 采集权限分别复用 `operations.records.read`、`assistant.feedback.manage` 和 `agent.test.execute`。
- 输入、输出、反馈和修正正文仅在 Application 调用期间短暂存在；持久化事实只保存 SHA-256。
- 同来源同版本同内容幂等；同版本内容漂移或来源版本倒退返回 `IDEMPOTENCY_CONFLICT`。
- 新来源版本追加样本版本和数据集快照，不改写历史；删除追加 tombstone，并从新快照移除活动成员。
- 授权决策、工作空间/部门/账号/资源范围、字段遮罩和最高密级随样本版本完整冻结。
- 跨工作空间、权限不足、资源范围不覆盖、字段被遮罩或密级越界一律失败关闭。

## 3. 证据边界

基线中的所有标识和正文均为合成数据。真实线上反馈与真实模型质量仍为 `not_run`，不能用本基线、本地 Mock Provider 或确定性摘要测试替代。

契约验证命令：

```bash
uv run pytest tests/test_p502_quality_contract.py tests/contract/test_contracts.py
```
