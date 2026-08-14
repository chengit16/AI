# 异步 Worker

本目录承载异步任务、文档解析、OCR、切片、Embedding 和索引处理。任务采用至少一次投递语义，业务事实状态保存在 PostgreSQL，所有处理器必须保证幂等。

本目录的新增和修改统一执行 [`docs/governance/backend-code-standards.md`](../../docs/governance/backend-code-standards.md)。

Worker 使用 Celery 5.5 和 Valkey Broker，PostgreSQL 仍是任务与业务状态的唯一事实来源。本地运行由 Celery Worker 与 Beat 共同承载四个版本化任务：

- `platform.outbox.dispatch.v1`：周期认领到期 Outbox，使用 `SKIP LOCKED`、短租约、有限指数退避和死信状态。
- `platform.integration.consume.v1`：验签内部任务信封，延续 W3C Trace，并在同一 PostgreSQL 事务写入消费回执与投影。
- `platform.ingestion.process.v1`：从 PostgreSQL 租约认领入库任务，执行来源摘要复核、解析、中文 OCR 和产物持久化。
- `platform.indexing.process.v1`：从成功解析事实幂等创建索引版本，生成权限元数据 Chunk、Embedding 和关键词索引。

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

## 文档入库、解析与 OCR

`modules/ingestion` 提供格式路由、Tika 结构化解析、PDF 基础表格补充解析和可替换的中文 OCR 边界。TXT 与 Markdown 使用本地确定性解析器；PDF、DOCX 和图片调用独立 Tika 服务，PDF 使用 pdfplumber 补充线框表格，中文图片当前使用 Tesseract `chi_sim+eng`。

`IngestionJob` 与 clean 上传事实同事务创建，按 `queued → running → retry_wait → succeeded/failed` 转换。Worker 通过行锁、`SKIP LOCKED` 和租约认领任务，默认最多执行三次。解析、OCR 和对象读写在数据库事务外执行；落状态只使用短事务，并对租约所有者做乐观复核。

来源对象读取后必须重新校验 SHA-256。解析产物使用确定性 JSON，写入 `workspaces/{workspace_id}/parsed/{version_id}/{job_id}.json`，不得包含来源对象键。解析任务只产生 Block Artifact，索引任务再消费该不可变产物，两个状态机不能合并。

大小、页数、Chunk 字符数和重叠量必须通过 `IngestionLimits` 注入，不能在业务流程中写死套餐额度。任务层使用稳定 `INGESTION_*` 错误码区分不可重试的内容问题与可重试的解析服务不可用。

固定全合成样本可重复生成：

```bash
uv run --locked python tests/fixtures/ingestion/generate_fixtures.py
tests/fixtures/ingestion/generate_chinese_fixture.sh
AI_PLATFORM_TEST_TIKA_URL=http://127.0.0.1:9998 \
  uv run --locked pytest tests/integration/test_p007_ingestion_pipeline.py
```

自定义 Tika 镜像固定 Apache Tika 基础摘要、`tesseract-ocr-chi-sim` 与 Noto CJK 版本，已使用全合成图片验收中文 OCR。`ChineseOcrAdapter` 后续可替换 PaddleOCR，无需改写任务状态机。

## Chunk、Embedding 与索引切换

`modules/indexing` 以 PostgreSQL `IndexVersion` 表达一次可追溯构建，状态按 `queued → running → retry_wait → ready → active/retired` 或 `failed` 转换。成功的 `IngestionJob` 会被幂等排队；重建生成递增 `build_no`，不会覆盖旧索引。

Worker 在数据库事务外读取并复核 Artifact、执行结构化 Chunk 和 Embedding，在短事务内落库。Chunk 固化部门、可见性、密级、权限标签、来源位置、Parser、OCR 和摘要，构建期间始终 `active=false`。文档发布或已发布版本构建完成时才原子切换当前索引；删除文档会同步撤销全部 Chunk 并终止在途构建。

本地默认 `DeterministicHashEmbeddingAdapter` 输出版本化 1024 维确定性向量，只用于功能闭环，不代表语义质量。后续接真实 Embedding 服务必须保持 `EmbeddingAdapter`、模型版本、维度校验、有限重试和数据外发策略边界。
