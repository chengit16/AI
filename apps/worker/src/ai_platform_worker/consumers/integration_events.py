"""注册 Outbox 发布、入库扫描、索引扫描和单事件幂等消费任务。"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_platform_backend.integration.consumer import IdempotentProjectionConsumer
from ai_platform_backend.integration.envelope import (
    HmacTaskEnvelopeSigner,
    InvalidTaskEnvelopeError,
    SigningKeyFile,
)
from ai_platform_backend.integration.trace import TraceContext
from celery import Task, shared_task

from ai_platform_worker.app.runtime import build_worker_runtime
from ai_platform_worker.config import get_worker_settings


@shared_task(name="platform.outbox.dispatch.v1", ignore_result=True)
def dispatch_outbox() -> dict[str, int]:
    """分发Outbox，遵守任务幂等、有限重试和提交时机约束。"""

    runtime = build_worker_runtime()
    try:
        result = runtime.dispatcher.dispatch_once()
        return {
            "claimed": result.claimed,
            "published": result.published,
            "retried": result.retried,
            "dead_lettered": result.dead_lettered,
        }
    finally:
        runtime.close()


def _process_ingestion_lane(lane: str) -> dict[str, int]:
    """执行单一入库 Lane，资源隔离由任务路由和数据库过滤共同保证。"""

    settings = get_worker_settings()
    runtime = build_worker_runtime(settings)
    try:
        result = runtime.ingestion.run_batch(
            lane="ocr" if lane == "ocr" else "parsing",
            limit=settings.ingestion_batch_size,
        )
        return {
            "claimed": result.claimed,
            "succeeded": result.succeeded,
            "retried": result.retried,
            "failed": result.failed,
            "lost_claims": result.lost_claims,
        }
    finally:
        runtime.close()


@shared_task(name="platform.ingestion.parse.v1", ignore_result=True)
def process_parsing_jobs() -> dict[str, int]:
    """处理 TXT、Markdown 与 DOCX 解析任务，不占用 OCR Worker。"""

    return _process_ingestion_lane("parsing")


@shared_task(name="platform.ingestion.ocr.v1", ignore_result=True)
def process_ocr_jobs() -> dict[str, int]:
    """处理 PDF 与图片 OCR 任务，不阻塞普通解析 Worker。"""

    return _process_ingestion_lane("ocr")


@shared_task(name="platform.indexing.embed.v1", ignore_result=True)
def process_index_embeddings() -> dict[str, int]:
    """生成并持久化不可见 Chunk 与 Embedding，不执行索引发布。"""

    settings = get_worker_settings()
    runtime = build_worker_runtime(settings)
    try:
        result = runtime.embedding.run_batch(limit=settings.indexing_batch_size)
        return {
            "enqueued": result.enqueued,
            "claimed": result.claimed,
            "succeeded": result.succeeded,
            "retried": result.retried,
            "failed": result.failed,
            "lost_claims": result.lost_claims,
        }
    finally:
        runtime.close()


@shared_task(name="platform.indexing.commit.v1", ignore_result=True)
def commit_index_versions() -> dict[str, int]:
    """提交已持久化的 Chunk 并原子切换索引版本。"""

    settings = get_worker_settings()
    runtime = build_worker_runtime(settings)
    try:
        result = runtime.indexing.run_batch(limit=settings.indexing_batch_size)
        return {
            "claimed": result.claimed,
            "succeeded": result.succeeded,
            "retried": result.retried,
            "failed": result.failed,
            "lost_claims": result.lost_claims,
        }
    finally:
        runtime.close()


@shared_task(name="platform.indexing.inspect.v1", ignore_result=True)
def inspect_index_consistency() -> dict[str, int]:
    """巡检索引引用并修复活动面；缺少完整候选时只排队安全重建。"""

    settings = get_worker_settings()
    runtime = build_worker_runtime(settings)
    try:
        report, repair = runtime.index_maintenance.inspect_and_repair()
        return {
            "scanned": report.scanned_document_count,
            "inconsistencies": report.inconsistency_count,
            "repaired": repair.repaired_document_count,
            "rebuild_queued": repair.rebuild_queued_count,
        }
    finally:
        runtime.close()


@shared_task(
    name="platform.integration.consume.v1",
    bind=True,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def consume_integration_event(self: Task, *, envelope: object) -> bool:
    """处理消费集成事件，遵守任务幂等、有限重试和提交时机约束。"""

    settings = get_worker_settings()
    signer = HmacTaskEnvelopeSigner(SigningKeyFile(settings.task_signing_key_path).load())
    signed = signer.verify(envelope)
    if signed.task_name != "platform.integration.consume.v1":
        raise InvalidTaskEnvelopeError("内部任务名称与消费者不匹配")

    trace = TraceContext.continue_from(signed.event.traceparent)
    runtime = build_worker_runtime(settings)
    try:
        consumer = IdempotentProjectionConsumer(
            "workspace-resource-projection-v1",
            runtime.consumers,
        )
        return consumer.handle(
            signed.event,
            task_id=signed.task_id,
            trace_id=trace.trace_id,
            traceparent=trace.traceparent,
            processed_at=datetime.now(UTC),
        )
    finally:
        runtime.close()
