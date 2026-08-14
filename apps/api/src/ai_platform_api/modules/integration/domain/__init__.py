"""集成事件领域的稳定公开对象。"""

from ai_platform_api.modules.integration.domain.events import IntegrationEvent, OutboxWriter

__all__ = ["IntegrationEvent", "OutboxWriter"]
