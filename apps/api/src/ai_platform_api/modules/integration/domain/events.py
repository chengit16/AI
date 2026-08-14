"""向 API 模块公开共享的集成事件和 Outbox 写入端口。"""

from ai_platform_backend.integration.domain import IntegrationEvent, OutboxWriter

__all__ = ["IntegrationEvent", "OutboxWriter"]
