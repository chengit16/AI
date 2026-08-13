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


@shared_task(
    name="platform.integration.consume.v1",
    bind=True,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def consume_integration_event(self: Task, *, envelope: object) -> bool:
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
