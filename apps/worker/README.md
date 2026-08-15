# 异步 Worker

本目录承载异步任务、文档解析、OCR、切片、Embedding 和索引处理。任务采用至少一次投递语义，业务事实状态保存在 PostgreSQL，所有处理器必须保证幂等。

本目录的新增和修改统一执行 [`docs/governance/backend-code-standards.md`](../../docs/governance/backend-code-standards.md)。

Worker 使用 Celery 5.5 和 Valkey Broker，PostgreSQL 仍是任务与业务状态的唯一事实来源。Scheduler 只负责周期唤醒，五个 Worker Lane 分别消费固定队列，共同承载六个版本化任务：

- `control`：`platform.outbox.dispatch.v1` 周期认领到期 Outbox；`platform.integration.consume.v1` 验签内部任务信封并幂等写入投影。
- `parsing`：`platform.ingestion.parse.v1` 只认领 TXT、Markdown 和 DOCX 入库任务。
- `ocr`：`platform.ingestion.ocr.v1` 只认领 PDF 和图片入库任务，避免 OCR 耗尽普通解析资源。
- `embedding`：`platform.indexing.embed.v1` 生成权限元数据 Chunk、Embedding 和关键词索引，产物保持不可见。
- `indexing`：`platform.indexing.commit.v1` 提交已生成的 Chunk，并原子切换索引版本和发布指针。

任务按至少一次投递设计。发布成功但确认失败、Worker 退出或 Broker 重投都可能产生重复任务，消费者必须用 `consumer_name + event_id` 保证业务副作用幂等，不能依赖 Broker 去重。

每个 Worker 只监听自己的 `platform.<lane>` 队列；即使任务被错误投递，PostgreSQL 认领条件也会再次校验 Lane 或阶段。并发参数分别由 `CONTROL_WORKER_CONCURRENCY`、`PARSING_WORKER_CONCURRENCY`、`OCR_WORKER_CONCURRENCY`、`EMBEDDING_WORKER_CONCURRENCY` 和 `INDEXING_WORKER_CONCURRENCY` 控制，应用层统一限制为 `1～8`。

本地只读健康信息：

```bash
uv run python -m ai_platform_worker.health
```

本地 Compose 会生成独立的 32 字节 `task-signing.key`。五个 Worker 只读挂载该密钥，API 只挂载平台主加密密钥，Scheduler 不接触任何文件型密钥；Broker 中的主体和 Trace 只有在任务信封 HMAC 验签成功后才可信。Worker 健康检查同时验证密钥长度和 Lane 进程，避免进程可响应但无法处理任务的假健康。

必须通过统一入口启动或重建服务，使相对数据目录先解析为绝对路径：

```bash
./platform start
./platform doctor
./platform logs worker-control
./platform logs worker-ocr
./platform logs scheduler
```

不要直接执行裸 `docker compose up` 重建服务；Compose 文件不负责把 `.env` 中的本地相对目录解析为项目根目录。

## 文档入库、解析与 OCR

`modules/ingestion` 提供格式路由、Tika 结构化解析、PDF 基础表格补充解析和可替换的中文 OCR 边界。TXT 与 Markdown 使用本地确定性解析器；PDF、DOCX 和图片调用独立 Tika 服务，PDF 使用 pdfplumber 补充线框表格，中文图片当前使用 Tesseract `chi_sim+eng`。

`IngestionJob` 与 clean 上传事实同事务创建，并将当前阶段和不可变 Attempt 历史保存在 PostgreSQL。Worker 通过行锁、`SKIP LOCKED`、Lane 条件和不可复用 `active_attempt_id` 认领任务，自动尝试默认最多三次，人工恢复最多三代。过期租约、同名 Worker 的迟到结果及终态重放都不能覆盖新执行。解析、OCR 和对象读写在数据库事务外执行；落状态只使用短事务，并对租约所有者做乐观复核。

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

`modules/indexing` 以 PostgreSQL `IndexVersion` 表达一次可追溯构建。Embedding 阶段认领 `queued/retry_wait`，成功后进入待提交状态；Indexing 阶段只认领该状态并完成发布。成功的 `IngestionJob` 会被幂等排队；重建生成递增 `build_no`，不会覆盖旧索引。

Embedding Worker 在数据库事务外读取并复核 Artifact、执行结构化 Chunk 和 Embedding，在短事务内落库。Chunk 固化部门、可见性、密级、权限标签、来源位置、Parser、OCR 和摘要，构建期间始终 `active=false`。Indexing Worker 只提交已完整落库的构建；文档发布或已发布版本构建完成时才原子切换当前索引。删除文档会同步撤销全部 Chunk 并终止在途构建。

索引自动尝试耗尽后进入稳定 `dead_letter`。受控人工恢复会开启新的恢复代次且最多执行三次；旧 Attempt、旧租约和并发重放均不能重复发布索引或改变当前发布指针。

本地默认 `DeterministicHashEmbeddingAdapter` 输出版本化 1024 维确定性向量，只用于功能闭环，不代表语义质量。后续接真实 Embedding 服务必须保持 `EmbeddingAdapter`、模型版本、维度校验、有限重试和数据外发策略边界。
