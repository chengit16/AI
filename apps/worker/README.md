# 异步 Worker

本目录承载异步任务、文档解析、OCR、切片、Embedding 和索引处理。任务采用至少一次投递语义，业务事实状态保存在 PostgreSQL，所有处理器必须保证幂等。

本目录的新增和修改统一执行 [`docs/governance/backend-code-standards.md`](../../docs/governance/backend-code-standards.md)。

阶段 0 最小健康检查：

```bash
uv run python -m ai_platform_worker.health
```

## 文档解析、OCR 与切片

`modules/ingestion` 提供格式路由、Tika 结构化解析、PDF 基础表格补充解析和结构感知切片。TXT 与 Markdown 使用本地确定性解析器；PDF、DOCX 和图片调用独立 Tika 服务，PDF 使用 pdfplumber 补充线框表格。所有 Chunk 都携带工作空间、知识库、文档版本、索引版本、部门、可见性、密级、来源位置和内容哈希。

大小、页数、Chunk 字符数和重叠量必须通过 `IngestionLimits` 注入，不能在业务流程中写死套餐额度。任务层使用稳定 `INGESTION_*` 错误码区分不可重试的内容问题与可重试的解析服务不可用。

固定全合成样本可重复生成：

```bash
uv run --locked python tests/fixtures/ingestion/generate_fixtures.py
AI_PLATFORM_TEST_TIKA_URL=http://127.0.0.1:9998 \
  uv run --locked pytest tests/integration/test_p007_ingestion_pipeline.py
```

阶段 0 的 Tika 镜像只包含英文、德文、法文、意大利文、日文、西班牙文和方向识别语言包；本节点已验证英文 Tesseract OCR 链路，但不代表中文 OCR 已通过。阶段 1D 需要接入 PaddleOCR 或等效中文 OCR Adapter，并使用中文扫描件重新验收。
