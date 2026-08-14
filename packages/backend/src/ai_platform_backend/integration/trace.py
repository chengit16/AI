"""提供跨 HTTP、Outbox 和 Worker 的 W3C Trace Context 校验与派生。"""

import re
import secrets
from dataclasses import dataclass

TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def _random_hex(byte_count: int) -> str:
    value = secrets.token_hex(byte_count)
    while int(value, 16) == 0:
        value = secrets.token_hex(byte_count)
    return value


@dataclass(frozen=True)
class TraceContext:
    """最小 W3C Trace Context，确保 HTTP、任务和事件使用同一传播规则。"""

    trace_id: str
    span_id: str
    trace_flags: str = "01"

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"

    @classmethod
    def new(cls) -> "TraceContext":
        return cls(trace_id=_random_hex(16), span_id=_random_hex(8))

    @classmethod
    def continue_from(cls, traceparent: str | None) -> "TraceContext":
        if traceparent is None:
            return cls.new()
        match = TRACEPARENT_PATTERN.fullmatch(traceparent.strip().lower())
        if (
            match is None
            or match.group("version") == "ff"
            or int(match.group("trace_id"), 16) == 0
            or int(match.group("span_id"), 16) == 0
        ):
            return cls.new()
        return cls(
            trace_id=match.group("trace_id"),
            span_id=_random_hex(8),
            trace_flags=match.group("flags"),
        )
