from dataclasses import dataclass
from uuid import UUID, uuid4

from ai_platform_api.common.trace import TraceContext


@dataclass(frozen=True)
class RequestContext:
    """服务端认证链生成的可信请求上下文。"""

    request_id: UUID
    trace: TraceContext
    actor_id: UUID
    workspace_id: UUID
    user_id: UUID | None
    authentication_method: str

    @classmethod
    def trusted(
        cls,
        *,
        actor_id: UUID,
        workspace_id: UUID,
        trace: TraceContext,
        user_id: UUID | None = None,
        authentication_method: str = "test",
        request_id: UUID | None = None,
    ) -> "RequestContext":
        return cls(
            request_id=request_id or uuid4(),
            trace=trace,
            actor_id=actor_id,
            workspace_id=workspace_id,
            user_id=user_id,
            authentication_method=authentication_method,
        )
