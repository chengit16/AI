"""在 HTTP 边缘建立或延续可信 Trace，并把响应标识返回调用方。"""

from time import monotonic
from uuid import UUID, uuid4

from ai_platform_backend.observability import ObservabilityRuntime
from ai_platform_backend.observability.runtime import trace_context_from_span
from opentelemetry.trace import Status, StatusCode
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

    def __init__(self, app: ASGIApp, *, observability: ObservabilityRuntime) -> None:
        self.app = app
        self.observability = observability

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 1. 先把所有调用方标识收敛为可信 Trace 与 UUID，业务层不能读取原始 Header。
        headers = MutableHeaders(scope=scope)
        parent_trace = TraceContext.continue_from(headers.get("traceparent"))
        request_id = _request_id(headers.get("x-request-id"))
        method = str(scope.get("method", "UNKNOWN")).upper()
        started = monotonic()
        response_status = 500

        # 2. 请求 Span 先于路由执行建立，应用服务通过标准 OTel Context 自动成为其子 Span。
        with self.observability.start_span(
            "http.server.request",
            component="http",
            operation="request",
            parent=parent_trace,
            attributes={"method": method, "request_id": str(request_id)},
        ) as span:
            trace_context = trace_context_from_span(span)
            scope.setdefault("state", {})["trace_context"] = trace_context
            scope["state"]["request_id"] = request_id
            token = self.observability.bind(request_id=request_id)

            # 3. 只在响应起始帧附加可信标识，同时捕获最终状态供低基数指标使用。
            async def send_with_trace(message: Message) -> None:
                nonlocal response_status
                if message["type"] == "http.response.start":
                    response_status = int(message["status"])
                    response_headers = MutableHeaders(scope=message)
                    response_headers["traceparent"] = trace_context.traceparent
                    response_headers["x-request-id"] = str(request_id)
                await send(message)

            # 4. 无论应用成功或异常都收口 Span、指标与日志，异常正文始终不进入观测属性。
            try:
                await self.app(scope, receive, send_with_trace)
            except Exception:
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                route = _route_template(scope)
                duration = monotonic() - started
                outcome = "success" if response_status < 400 else "error"
                safe_span_fields = self.observability.fields.validate(
                    "span",
                    {
                        "route": route,
                        "http_status_code": response_status,
                        "outcome": outcome,
                    },
                )
                for key, value in safe_span_fields.items():
                    span.set_attribute(f"platform.{key}", value)
                labels = {
                    **self.observability.common_labels,
                    "method": method,
                    "route": route,
                }
                self.observability.fields.validate(
                    "metric",
                    {**labels, "http_status_code": str(response_status)},
                )
                self.observability.http_requests.labels(
                    **labels,
                    http_status_code=str(response_status),
                ).inc()
                self.observability.http_duration.labels(**labels).observe(duration)
                self.observability.log(
                    "http_request_completed",
                    component="http",
                    operation="request",
                    outcome=outcome,
                    method=method,
                    route=route,
                    http_status_code=response_status,
                    duration_ms=round(duration * 1000, 3),
                )
                self.observability.reset(token)


def _route_template(scope: Scope) -> str:
    """优先使用 Router 模板，未知路由统一折叠，禁止把真实资源 ID 写入标签。"""

    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"
