# 阶段 0 安全评估 Fixture

`p0-11-v1.json` 是阶段 0 建立的全合成 AI/RAG 安全回归数据集，供阶段 1D/1E 的确定性防护测试和后续真实模型安全评估复用。

## 数据边界

- 所有 `workspace_id`、文档 ID、事件 ID、文本和敏感标记均为合成值。
- `SYNTHETIC_SECRET_A`、`SYNTHETIC_WORKSPACE_B_DATA` 等标记只用于验证禁止泄漏，不能替换成真实密钥或客户资料。
- Fixture 不调用真实模型供应商、外部工具或真实企业数据。

## 版本和覆盖范围

当前版本为 `p0-11-v1`，覆盖十类样本：直接 Prompt Injection、间接 Prompt Injection、跨工作空间召回、撤权/旧版本引用、字段级泄漏、引用伪造、混淆绕过、查询改写与全文精读越权、SSE 重复生成与回放预算、模型输出越权。

每条样本都声明预期安全结果、稳定错误码、允许暴露内容、禁止泄漏标记、工作空间范围和字段掩码。阶段 1 实现防护时，应保持这些安全不变量；若业务需要改变预期，必须先更新威胁模型和 ADR，再修改 Fixture 版本。

## 验证命令

```bash
uv run --locked pytest tests/test_p011_security_dataset.py
```

完整阶段门禁仍使用仓库根目录的 `./scripts/verify`。
