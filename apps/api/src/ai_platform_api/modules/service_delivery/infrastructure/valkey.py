"""使用 Valkey 原子固定窗口限制服务调用，并对幂等重试免重复计数。"""

from __future__ import annotations

import hashlib
from typing import Protocol, cast
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError

from ai_platform_api.modules.service_delivery.domain.models import (
    ServiceInvocationRateLimitedError,
    ServiceInvocationRateLimitUnavailableError,
)

_CONSUME_SCRIPT = """
if redis.call('EXISTS', KEYS[2]) == 1 then
  return 0
end
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if current > tonumber(ARGV[1]) then
  return -1
end
redis.call('SET', KEYS[2], '1', 'EX', ARGV[2])
return current
"""


class RateLimitClient(Protocol):
    """约束固定窗口限流所需的最小 Valkey 客户端能力。"""

    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...

    def close(self) -> None: ...


class ValkeyInvocationRateLimiter:
    """按脱敏 Actor 组合键执行失败关闭的每分钟固定窗口限流。"""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: RateLimitClient | None = None,
        limit_per_minute: int = 60,
        timeout_seconds: float = 0.5,
    ) -> None:
        if client is None and url is None:
            raise ValueError("服务调用限流必须配置 Valkey URL 或客户端")
        if not 1 <= limit_per_minute <= 10_000:
            raise ValueError("服务调用每分钟限制必须位于 1 到 10000 之间")
        self._client = client or cast(
            RateLimitClient,
            Redis.from_url(
                str(url),
                decode_responses=True,
                socket_connect_timeout=timeout_seconds,
                socket_timeout=timeout_seconds,
                health_check_interval=30,
            ),
        )
        self._limit_per_minute = limit_per_minute

    def consume(
        self,
        workspace_id: UUID,
        service_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
    ) -> None:
        """原子占用一次窗口额度；相同 Actor 与幂等键重试直接复用。"""

        principal_digest = _digest(workspace_id.bytes, service_id.bytes, actor_id.bytes)
        request_digest = _digest(principal_digest.encode(), idempotency_key.encode())
        try:
            result = self._client.eval(
                _CONSUME_SCRIPT,
                2,
                f"service-invocation-rate:v1:{principal_digest}",
                f"service-invocation-replay:v1:{request_digest}",
                self._limit_per_minute,
                60,
            )
        except RedisError as error:
            raise ServiceInvocationRateLimitUnavailableError from error
        if isinstance(result, bool) or not isinstance(result, int):
            raise ServiceInvocationRateLimitUnavailableError
        if result < 0:
            raise ServiceInvocationRateLimitedError

    def close(self) -> None:
        self._client.close()


def _digest(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(4, "big"))
        digest.update(part)
    return digest.hexdigest()
