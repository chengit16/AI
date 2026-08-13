# 异步 Worker

本目录承载异步任务、文档解析、OCR、切片、Embedding 和索引处理。任务采用至少一次投递语义，业务事实状态保存在 PostgreSQL，所有处理器必须保证幂等。

本目录的新增和修改统一执行 [`docs/governance/backend-code-standards.md`](../../docs/governance/backend-code-standards.md)。

阶段 0 最小健康检查：

```bash
uv run python -m ai_platform_worker.health
```
