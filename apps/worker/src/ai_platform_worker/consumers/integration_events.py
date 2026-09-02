"""注册 Outbox 发布、入库扫描、索引扫描和单事件幂等消费任务。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from ai_platform_backend.integration.consumer import IdempotentProjectionConsumer
from ai_platform_backend.integration.domain import IntegrationEvent
from ai_platform_backend.integration.envelope import (
    HmacTaskEnvelopeSigner,
    InvalidTaskEnvelopeError,
    SigningKeyFile,
)
from ai_platform_backend.integration.trace import TraceContext
from ai_platform_backend.observability.runtime import trace_context_from_span
from celery import Task, shared_task
from sqlalchemy.exc import SQLAlchemyError

from ai_platform_worker.app.runtime import WorkerRuntime, build_worker_runtime
from ai_platform_worker.config import get_worker_settings
from ai_platform_worker.modules.knowledge.domain.object_cleanup import (
    TRASH_PURGE_REQUESTED_EVENT,
    ObjectCleanupUnavailableError,
)
from ai_platform_worker.observability import get_worker_observability

_OBJECT_CLEANUP_CONSUMER = "knowledge-object-cleanup-v1"


def _queue_name() -> str:
    """把进程 Lane 映射为固定低基数队列标签。"""

    return f"platform.{get_worker_settings().worker_lane}"


@shared_task(name="platform.outbox.dispatch.v1", ignore_result=True)
def dispatch_outbox() -> dict[str, int]:
    """分发Outbox，遵守任务幂等、有限重试和提交时机约束。"""

    # 1. 调度 Span 包裹领取、发布和结果采集，单条事件标识仍只保留在事实库。
    observability = get_worker_observability()
    with observability.task(task_name="platform.outbox.dispatch.v1", queue=_queue_name()):
        runtime = build_worker_runtime()
        try:
            result = runtime.dispatcher.dispatch_once()
            # 四类结果使用固定 outcome 标签，事件 ID 与 Broker 错误不会进入指标。
            for outcome, value in {
                "claimed": result.claimed,
                "published": result.published,
                "retried": result.retried,
                "dead_lettered": result.dead_lettered,
            }.items():
                observability.outbox_dispatch.labels(
                    **observability.common_labels,
                    outcome=outcome,
                ).inc(value)
            observability.outbox_pending.labels(**observability.common_labels).set(
                result.pending_count
            )
            observability.outbox_oldest_pending_age.labels(**observability.common_labels).set(
                result.oldest_pending_age_seconds
            )
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
    observability = get_worker_observability()
    task_name = "platform.ingestion.ocr.v1" if lane == "ocr" else "platform.ingestion.parse.v1"
    with observability.task(task_name=task_name, queue=_queue_name()):
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
    observability = get_worker_observability()
    with observability.task(task_name="platform.indexing.embed.v1", queue=_queue_name()):
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
    observability = get_worker_observability()
    with observability.task(task_name="platform.indexing.commit.v1", queue=_queue_name()):
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
    observability = get_worker_observability()
    with observability.task(task_name="platform.indexing.inspect.v1", queue=_queue_name()):
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


@shared_task(name="platform.indexing.maintenance_commands.v1", ignore_result=True)
def process_index_maintenance_commands() -> dict[str, int]:
    """处理受控工作台登记的索引巡检、全量重建和不可恢复 Chunk 清理请求。"""

    settings = get_worker_settings()
    observability = get_worker_observability()
    task_name = "platform.indexing.maintenance_commands.v1"
    with observability.task(task_name=task_name, queue=_queue_name()):
        runtime = build_worker_runtime(settings)
        try:
            result = runtime.index_maintenance_commands.run_batch(
                limit=settings.indexing_batch_size
            )
            return {
                "claimed": result.claimed,
                "completed": result.completed,
                "retried": result.retried,
                "dead_lettered": result.dead_lettered,
            }
        finally:
            runtime.close()


@shared_task(name="platform.operations.audit_exports.v1", ignore_result=True)
def process_audit_exports() -> dict[str, int]:
    """处理审计导出请求，结果只保留脱敏投影行数和校验摘要。"""

    settings = get_worker_settings()
    observability = get_worker_observability()
    task_name = "platform.operations.audit_exports.v1"
    with observability.task(task_name=task_name, queue=_queue_name()):
        runtime = build_worker_runtime(settings)
        try:
            result = runtime.audit_exports.run_batch(limit=settings.audit_export_batch_size)
            return {
                "claimed": result.claimed,
                "completed": result.completed,
                "retried": result.retried,
                "dead_lettered": result.dead_lettered,
            }
        finally:
            runtime.close()


@shared_task(name="platform.knowledge.trash_retention.v1", ignore_result=True)
def purge_knowledge_trash(*, workspace_id: str | None = None) -> dict[str, int]:
    """清理超过保留期的回收站文档，并为对象/索引清理登记待处理事件。"""

    settings = get_worker_settings()
    observability = get_worker_observability()
    with observability.task(
        task_name="platform.knowledge.trash_retention.v1",
        queue=_queue_name(),
    ):
        parsed_workspace_id = UUID(workspace_id) if workspace_id else None
        cutoff = datetime.now(UTC) - timedelta(days=settings.knowledge_trash_retention_days)
        runtime = build_worker_runtime(settings)
        try:
            result = runtime.trash_retention.run(
                workspace_id=parsed_workspace_id,
                cutoff=cutoff,
                batch_size=settings.knowledge_trash_retention_batch_size,
            )
            return {
                "scanned": result.scanned,
                "purged": result.purged,
                "external_cleanup_requested": result.external_cleanup_requested,
            }
        finally:
            runtime.close()


@shared_task(
    name="platform.integration.consume.v1",
    bind=True,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(ObjectCleanupUnavailableError, SQLAlchemyError),
    retry_backoff=5,
    retry_backoff_max=60,
    retry_jitter=False,
    max_retries=3,
)
def consume_integration_event(self: Task, *, envelope: object) -> bool:
    """处理消费集成事件，遵守任务幂等、有限重试和提交时机约束。"""

    # 1. 外层任务记录 Broker 交付和验签失败，但不会从未验证载荷恢复任何追踪上下文。
    settings = get_worker_settings()
    observability = get_worker_observability()
    with observability.task(
        task_name="platform.integration.consume.v1",
        queue=_queue_name(),
    ):
        signer = HmacTaskEnvelopeSigner(SigningKeyFile(settings.task_signing_key_path).load())
        signed = signer.verify(envelope)
        if signed.task_name != "platform.integration.consume.v1":
            raise InvalidTaskEnvelopeError("内部任务名称与消费者不匹配")

        # 2. 只有验签后的事件才允许恢复父 Trace；内层任务 Span 因此与 Outbox 事实同链。
        parent_trace = TraceContext.continue_from(signed.event.traceparent)
        with observability.start_span(
            "worker.task.integration.consume",
            component="task",
            operation="execute",
            parent=parent_trace,
            attributes={
                "task_name": "platform.integration.consume.v1",
                "queue": _queue_name(),
                "task_id": str(signed.task_id),
            },
        ) as span:
            token = observability.bind(task_id=signed.task_id)
            try:
                runtime = build_worker_runtime(settings)
                try:
                    current_trace = trace_context_from_span(span)
                    if signed.event.event_type == TRASH_PURGE_REQUESTED_EVENT:
                        # 外部副作用成功后才写回执；进程中断会重放幂等删除，不会提前吞掉事件。
                        runtime.object_cleanup.handle(signed.event)
                        return _record_object_cleanup_receipt(
                            runtime,
                            signed.event,
                            task_id=signed.task_id,
                            trace_id=current_trace.trace_id,
                            traceparent=current_trace.traceparent,
                        )
                    consumer = IdempotentProjectionConsumer(
                        "workspace-resource-projection-v1",
                        runtime.consumers,
                    )
                    return consumer.handle(
                        signed.event,
                        task_id=signed.task_id,
                        trace_id=current_trace.trace_id,
                        traceparent=current_trace.traceparent,
                        processed_at=datetime.now(UTC),
                    )
                finally:
                    runtime.close()
            finally:
                observability.reset(token)


def _record_object_cleanup_receipt(
    runtime: WorkerRuntime,
    event: IntegrationEvent,
    *,
    task_id: UUID,
    trace_id: str,
    traceparent: str,
) -> bool:
    """对象删除成功后提交专用回执，避免为已删除文档重新创建通用投影。"""

    with runtime.consumers as unit_of_work:
        claimed = unit_of_work.claim(
            _OBJECT_CLEANUP_CONSUMER,
            event,
            task_id=task_id,
            trace_id=trace_id,
            traceparent=traceparent,
            processed_at=datetime.now(UTC),
        )
        unit_of_work.commit()
        return claimed
