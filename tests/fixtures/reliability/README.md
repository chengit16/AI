# 阶段 2 可靠性故障 Fixture

`p2-01-v1.json` 是阶段 2 的全合成、确定性故障注入基线。它覆盖超时、重复、乱序、部分失败、依赖不可用、恢复完整性、权限传播和可观测性泄漏八类故障，并至少提供十二个可执行场景。

Fixture 只描述稳定前置条件、注入点、步骤、预期终态和证据来源，不绑定某个测试框架。`P2-02`～`P2-10` 分节点建立对应能力，`p2-11-v1.json` 再把数据库、对象、索引、Valkey、Worker、API 实例、SSE、Outbox 和恢复后授权边界映射到 12 个固定 pytest 节点，作为同一次本地联合演练的机器可执行清单。

`p2-11-v1` 不复制恢复实现，也不允许从 Fixture 执行任意命令。入口先校验 Schema、P2-01 场景引用、可靠性不变量、组件覆盖、测试文件和函数 AST，再一次性运行清单并解析 JUnit；场景缺失、重复、跳过、失败或无有效证据均失败关闭。

所有内容必须使用合成工作空间、文档、事件、对象和敏感标记，禁止替换成真实客户资料、真实凭据或外部模型供应商数据。

```bash
uv run --locked pytest tests/test_p201_reliability_baseline.py tests/test_p211_reliability_drill.py
./platform drill-stage-2
```
