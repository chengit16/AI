"""向 API 模块公开共享的幂等投影消费者契约与实现。"""

from ai_platform_backend.integration.consumer import (
    ConsumerUnitOfWork,
    IdempotentProjectionConsumer,
)

__all__ = ["ConsumerUnitOfWork", "IdempotentProjectionConsumer"]
