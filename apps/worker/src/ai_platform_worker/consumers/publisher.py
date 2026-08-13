from datetime import UTC, datetime
from uuid import uuid4

from ai_platform_backend.integration.domain import IntegrationEvent
from ai_platform_backend.integration.envelope import HmacTaskEnvelopeSigner


class CeleryTaskPublisher:
    """只发布签名后的语言无关信封，Broker 不承载可信身份判断。"""

    def __init__(self, signer: HmacTaskEnvelopeSigner) -> None:
        self._signer = signer

    def publish(self, event: IntegrationEvent) -> None:
        # 延迟导入避免共享运行时初始化时反向触发 Celery 模块装配。
        from ai_platform_worker.app.celery_app import celery_app

        task_id = uuid4()
        envelope = self._signer.issue(
            task_name="platform.integration.consume.v1",
            task_id=task_id,
            issued_at=datetime.now(UTC),
            event=event,
        )
        celery_app.send_task(
            envelope.task_name,
            kwargs={"envelope": envelope.to_dict()},
            task_id=str(task_id),
        )
