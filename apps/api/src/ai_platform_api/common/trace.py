"""解析、校验并生成符合 W3C Trace Context 的追踪事实。"""

from ai_platform_backend.integration.trace import TRACEPARENT_PATTERN, TraceContext

__all__ = ["TRACEPARENT_PATTERN", "TraceContext"]
