"""在 HTTP 边缘建立或延续可信 Trace，并把响应标识返回调用方。"""

from uuid import UUID, uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ai_platform_api.common.trace import TraceContext


def _request_id(raw_value: str | None) -> UUID:
    if raw_value is not None:
        try:
            return UUID(raw_value)
        except ValueError:
            pass
    return uuid4()


class TraceContextMiddleware:
    """在 HTTP 边缘校验并延续 Trace，业务代码只读取可信解析结果。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = MutableHeaders(scope=scope)
        trace = TraceContext.continue_from(headers.get("traceparent"))
        request_id = _request_id(headers.get("x-request-id"))
        scope.setdefault("state", {})["trace_context"] = trace
        scope["state"]["request_id"] = request_id

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["traceparent"] = trace.traceparent
                response_headers["x-request-id"] = str(request_id)
            await send(message)

        await self.app(scope, receive, send_with_trace)
