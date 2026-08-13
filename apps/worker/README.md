# 异步 Worker

本目录承载异步任务、文档解析、OCR、切片、Embedding 和索引处理。任务采用至少一次投递语义，业务事实状态保存在 PostgreSQL，所有处理器必须保证幂等。

本目录的新增和修改统一执行 [`docs/governance/backend-code-standards.md`](../../docs/governance/backend-code-standards.md)。

Worker 使用 Celery 5.5 和 Valkey Broker，PostgreSQL 仍是任务与业务状态的唯一事实来源。本地运行由 Celery Worker 与 Beat 共同承载两个版本化任务：

- `platform.outbox.dispatch.v1`：周期认领到期 Outbox，使用 `SKIP LOCKED`、短租约、有限指数退避和死信状态。
- `platform.integration.consume.v1`：验签内部任务信封，延续 W3C Trace，并在同一 PostgreSQL 事务写入消费回执与投影。

任务按至少一次投递设计。发布成功但确认失败、Worker 退出或 Broker 重投都可能产生重复任务，消费者必须用 `consumer_name + event_id` 保证业务副作用幂等，不能依赖 Broker 去重。

本地只读健康信息：

```bash
uv run python -m ai_platform_worker.health
```

本地 Compose 会生成独立的 32 字节 `task-signing.key`。只有 Worker 挂载该密钥，API 只挂载平台主加密密钥；Broker 中的主体和 Trace 只有在任务信封 HMAC 验签成功后才可信。可用以下命令检查真实 Worker 与全部服务：

```bash
./platform doctor
docker compose --env-file .env -f infra/compose/compose.yaml exec -T worker \
  uv run --no-sync celery -A ai_platform_worker.app.celery_app:celery_app \
  inspect registered --timeout 3
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
