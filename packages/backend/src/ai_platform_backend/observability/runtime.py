"""提供结构化日志、OpenTelemetry Span、Prometheus 指标与告警事实。"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from time import monotonic
from typing import ParamSpec, TextIO, TypeVar
from uuid import UUID

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
    TraceState,
)
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
    values,
)

from ai_platform_backend.integration.trace import TraceContext
from ai_platform_backend.observability.fields import (
    ObservabilityFieldRegistry,
    ObservationScalar,
)

P = ParamSpec("P")
R = TypeVar("R")
_PROCESS_PREFIX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class AlertFact:
    """表示已通过字段注册表的告警事实，不包含原始异常或业务正文。"""

    fields: dict[str, ObservationScalar]


@dataclass(frozen=True)
class _BoundContext:
    """保存当前执行单元的可信关联标识，供日志自动补齐。"""

    runtime: ObservabilityRuntime
    request_id: UUID | None = None
    task_id: UUID | None = None


_BOUND_CONTEXT: ContextVar[_BoundContext | None] = ContextVar(
    "ai_platform_observability_context",
    default=None,
)


class SafeJsonFormatter(logging.Formatter):
    """只序列化运行时已验证字段，避免异常对象和自由文本旁路注册表。"""

    def __init__(self, *, service_name: str, environment: str) -> None:
        super().__init__()
        self._service_name = service_name
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "observability_fields", None)
        if not isinstance(fields, dict):
            fields = {
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "level": record.levelname.lower(),
                "event_name": "unstructured_library_log",
                "service": self._service_name,
                "environment": self._environment,
            }
            bound = _BOUND_CONTEXT.get()
            if bound is not None:
                if bound.request_id is not None:
                    fields["request_id"] = str(bound.request_id)
                if bound.task_id is not None:
                    fields["task_id"] = str(bound.task_id)
            span_context = trace.get_current_span().get_span_context()
            if span_context.is_valid:
                fields["trace_id"] = trace.format_trace_id(span_context.trace_id)
                fields["span_id"] = trace.format_span_id(span_context.span_id)
        return json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def configure_safe_standard_logging(
    *,
    service_name: str,
    environment: str,
    replace_handlers: bool = False,
) -> None:
    """把框架与第三方 Logger 收敛为无自由文本 JSON，平台事件仍使用专用 Handler。"""

    formatter = SafeJsonFormatter(service_name=service_name, environment=environment)
    targets = [
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("celery"),
        logging.getLogger("celery.task"),
    ]
    for logger in targets:
        if replace_handlers:
            logger.handlers.clear()
        if logger.handlers:
            for handler in logger.handlers:
                handler.setFormatter(formatter)
        elif logger is targets[0] or replace_handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            logger.addHandler(handler)


class ObservabilityRuntime:
    """按进程隔离 Trace Provider 与指标注册表，并统一执行字段安全校验。"""

    def __init__(
        self,
        *,
        service_name: str,
        environment: str,
        field_registry_path: str | Path,
        otlp_endpoint: str | None = None,
        otlp_timeout_seconds: float = 2.0,
        span_exporter: SpanExporter | None = None,
        log_stream: TextIO | None = None,
        metrics_process_prefix: str = "process",
        configure_library_logging: bool = False,
    ) -> None:
        # 1. 先固定服务身份与跨容器唯一进程前缀，再选择单进程或共享文件指标实现。
        self.service_name = service_name
        self.environment = environment
        if not _PROCESS_PREFIX_PATTERN.fullmatch(metrics_process_prefix):
            raise ValueError("Prometheus 进程前缀必须是小写字母、数字或连字符")
        self._metrics_process_prefix = metrics_process_prefix
        self._multiprocess_path = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
        if self._multiprocess_path is not None:
            # 容器 PID 命名空间可能产生相同 PID，服务前缀保证共享目录内文件名仍唯一。
            values.ValueClass = values.MultiProcessValue(  # type: ignore[no-untyped-call]
                lambda: f"{self._metrics_process_prefix}-{os.getpid()}"
            )
        # 2. 字段注册表必须先于导出器加载；随后按配置装配本地或 OTLP Span Processor。
        self.fields = ObservabilityFieldRegistry.load(field_registry_path)
        self._provider = TracerProvider(
            resource=Resource.create(
                {"service.name": service_name, "deployment.environment.name": environment}
            )
        )
        if span_exporter is not None:
            self._provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        if otlp_endpoint is not None:
            exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint,
                timeout=otlp_timeout_seconds,
            )
            self._provider.add_span_processor(BatchSpanProcessor(exporter))
        # 3. 最后创建指标和 JSON Handler，框架日志接管保持为显式配置而非测试副作用。
        self.tracer = self._provider.get_tracer("ai-platform", "1.0")
        self.registry = CollectorRegistry(auto_describe=True)
        self._configure_metrics()
        if configure_library_logging:
            configure_safe_standard_logging(
                service_name=service_name,
                environment=environment,
            )
        # 每个运行时持有独立 Logger，测试和重复应用工厂不会复用旧 Handler 或输出目标。
        self._logger = logging.Logger(f"ai_platform.observability.{service_name}")
        self._logger.propagate = False
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(SafeJsonFormatter(service_name=service_name, environment=environment))
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)

    def _configure_metrics(self) -> None:
        """集中创建低基数指标，业务调用方不能临时增加标签。"""

        # 1. HTTP、应用操作与任务共享服务/环境标签，资源 ID 只能进入业务事实和审计。
        common = ("service", "environment")
        self.http_requests = Counter(
            "ai_platform_http_requests_total",
            "HTTP 请求结果数量。",
            (*common, "method", "route", "http_status_code"),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "ai_platform_http_request_duration_seconds",
            "HTTP 请求耗时。",
            (*common, "method", "route"),
            registry=self.registry,
        )
        self.operations = Counter(
            "ai_platform_operations_total",
            "关键应用操作结果数量。",
            (*common, "component", "operation", "outcome"),
            registry=self.registry,
        )
        self.operation_duration = Histogram(
            "ai_platform_operation_duration_seconds",
            "关键应用操作耗时。",
            (*common, "component", "operation"),
            registry=self.registry,
        )
        self.tasks = Counter(
            "ai_platform_tasks_total",
            "Worker 任务结果数量。",
            (*common, "task_name", "queue", "outcome"),
            registry=self.registry,
        )
        self.task_duration = Histogram(
            "ai_platform_task_duration_seconds",
            "Worker 任务耗时。",
            (*common, "task_name", "queue"),
            registry=self.registry,
        )
        # 2. 依赖和 SSE 指标只表达稳定状态、模式与耗时，不把频道或 Run 作为维度。
        self.dependency_health = Gauge(
            "ai_platform_dependency_health",
            "依赖健康状态, 1 表示正常, 0 表示降级。",
            (*common, "dependency"),
            registry=self.registry,
            multiprocess_mode="livemostrecent",
        )
        self.dependency_duration = Gauge(
            "ai_platform_dependency_check_duration_seconds",
            "最近一次依赖检查耗时。",
            (*common, "dependency"),
            registry=self.registry,
            multiprocess_mode="livemostrecent",
        )
        self.sse_notifications = Counter(
            "ai_platform_sse_notifications_total",
            "SSE 通知发布、接收和降级结果数量。",
            (*common, "notification_mode", "outcome"),
            registry=self.registry,
        )
        self.sse_wakeup_duration = Histogram(
            "ai_platform_sse_wakeup_duration_seconds",
            "SSE 唤醒到事实回查耗时。",
            (*common, "notification_mode"),
            registry=self.registry,
        )
        self.sse_active_subscriptions = Gauge(
            "ai_platform_sse_active_subscriptions",
            "当前活动 SSE 通知订阅数。",
            common,
            registry=self.registry,
            multiprocess_mode="livesum",
        )
        self.sse_fallback_queries = Counter(
            "ai_platform_sse_fallback_queries_total",
            "SSE 通知缺失或不可用时的 PostgreSQL 兜底查询数。",
            common,
            registry=self.registry,
        )
        # 3. Outbox 指标记录批次结果与整体积压，事件标识和 Payload 不进入 Prometheus。
        self.outbox_dispatch = Counter(
            "ai_platform_outbox_dispatch_total",
            "Outbox 本轮发布、重试和死信数量。",
            (*common, "outcome"),
            registry=self.registry,
        )
        self.outbox_pending = Gauge(
            "ai_platform_outbox_pending_events",
            "Outbox 调度开始时尚未完成的事件数量。",
            common,
            registry=self.registry,
            multiprocess_mode="livemostrecent",
        )
        self.outbox_oldest_pending_age = Gauge(
            "ai_platform_outbox_oldest_pending_age_seconds",
            "Outbox 调度开始时最老未完成事件的年龄。",
            common,
            registry=self.registry,
            multiprocess_mode="livemostrecent",
        )
        # 4. 授权指标只使用固定表面、结论和原因码，禁止主体、空间或资源标识成为标签。
        self.authorization_decisions = Counter(
            "ai_platform_authorization_decisions_total",
            "授权表面的允许、拒绝与稳定原因数量。",
            (*common, "surface", "outcome", "reason_code"),
            registry=self.registry,
        )
        self.authorization_policy_cache = Counter(
            "ai_platform_authorization_policy_cache_total",
            "策略版本见证的新建、命中与失败关闭数量。",
            (*common, "cache_status"),
            registry=self.registry,
        )

    @property
    def common_labels(self) -> dict[str, str]:
        """返回固定服务与环境标签的副本，调用方不能改变运行时基线。"""

        return {"service": self.service_name, "environment": self.environment}

    def bind(
        self,
        *,
        request_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> Token[_BoundContext | None]:
        """绑定当前请求或任务标识；Token 由调用方在 finally 中恢复。"""

        return _BOUND_CONTEXT.set(_BoundContext(self, request_id=request_id, task_id=task_id))

    def reset(self, token: Token[_BoundContext | None]) -> None:
        """恢复外层执行上下文，防止线程或协程复用时串联无关请求。"""

        _BOUND_CONTEXT.reset(token)

    @contextmanager
    def start_span(
        self,
        name: str,
        *,
        component: str,
        operation: str,
        parent: TraceContext | None = None,
        attributes: dict[str, ObservationScalar] | None = None,
    ) -> Iterator[Span]:
        """创建当前 Span；显式父 Trace 只接受已通过 W3C 校验的上下文。"""

        parent_context = _otel_parent_context(parent) if parent is not None else None
        safe_attributes = self.fields.validate(
            "span",
            {"component": component, "operation": operation, **(attributes or {})},
        )
        with self.tracer.start_as_current_span(
            name,
            context=parent_context,
            attributes={f"platform.{key}": value for key, value in safe_attributes.items()},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            yield span

    @contextmanager
    def operation(self, *, component: str, operation: str) -> Iterator[Span]:
        """记录关键应用操作的 Span、耗时和稳定结果，不采集调用参数或返回正文。"""

        # 1. Span 只接收固定组件与操作，异常时仅记录稳定错误码。
        started = monotonic()
        outcome = "success"
        with self.start_span(
            f"{component}.{operation}",
            component=component,
            operation=operation,
        ) as span:
            try:
                yield span
            except Exception as error:
                outcome = "error"
                error_code = _stable_error_code(error)
                failure = self.fields.validate(
                    "span",
                    {"outcome": outcome, "error_code": error_code},
                )
                for key, value in failure.items():
                    span.set_attribute(f"platform.{key}", value)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                # 2. 成功和失败均写入同一低基数指标，并在 Span 结束前输出关联日志。
                duration = monotonic() - started
                labels = {
                    **self.common_labels,
                    "component": component,
                    "operation": operation,
                }
                self.fields.validate("metric", {**labels, "outcome": outcome})
                self.operations.labels(**labels, outcome=outcome).inc()
                self.operation_duration.labels(**labels).observe(duration)
                self.log(
                    "operation_completed",
                    component=component,
                    operation=operation,
                    outcome=outcome,
                    duration_ms=round(duration * 1000, 3),
                )

    @contextmanager
    def task(
        self,
        *,
        task_name: str,
        queue: str,
        task_id: UUID | None = None,
        parent: TraceContext | None = None,
    ) -> Iterator[Span]:
        """记录一个 Celery 执行单元；任务参数和异常正文不会进入 Span 或日志。"""

        # 1. 任务上下文先绑定可信任务 ID，再建立不含任务参数的执行 Span。
        started = monotonic()
        outcome = "success"
        token = self.bind(task_id=task_id)
        try:
            with self.start_span(
                "worker.task",
                component="task",
                operation="execute",
                parent=parent,
                attributes={
                    "task_name": task_name,
                    "queue": queue,
                    **({"task_id": str(task_id)} if task_id is not None else {}),
                },
            ) as span:
                try:
                    yield span
                except Exception as error:
                    outcome = "error"
                    error_code = _stable_error_code(error)
                    failure = self.fields.validate(
                        "span",
                        {"outcome": outcome, "error_code": error_code},
                    )
                    for key, value in failure.items():
                        span.set_attribute(f"platform.{key}", value)
                    span.set_status(Status(StatusCode.ERROR))
                    raise
                finally:
                    # 2. 所有终态都回写任务耗时和结果；finally 之后恢复外层协程上下文。
                    duration = monotonic() - started
                    labels = {
                        **self.common_labels,
                        "task_name": task_name,
                        "queue": queue,
                    }
                    self.fields.validate("metric", {**labels, "outcome": outcome})
                    self.tasks.labels(**labels, outcome=outcome).inc()
                    self.task_duration.labels(**labels).observe(duration)
                    self.log(
                        "worker_task_completed",
                        component="task",
                        operation="execute",
                        task_name=task_name,
                        queue=queue,
                        outcome=outcome,
                        duration_ms=round(duration * 1000, 3),
                    )
        finally:
            self.reset(token)

    def log(
        self,
        event_name: str,
        *,
        level: int = logging.INFO,
        **attributes: ObservationScalar,
    ) -> None:
        """输出单行 JSON 事件；调用方不能传异常文本、正文或未登记字段。"""

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        fields: dict[str, ObservationScalar] = {
            "timestamp": now,
            "level": logging.getLevelName(level).lower(),
            "event_name": event_name,
            "service": self.service_name,
            "environment": self.environment,
            **attributes,
        }
        bound = _BOUND_CONTEXT.get()
        if bound is not None and bound.runtime is self:
            if bound.request_id is not None:
                fields["request_id"] = str(bound.request_id)
            if bound.task_id is not None:
                fields["task_id"] = str(bound.task_id)
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            fields["trace_id"] = trace.format_trace_id(span_context.trace_id)
            fields["span_id"] = trace.format_span_id(span_context.span_id)
        safe = self.fields.validate("log", fields)
        self._logger.log(level, event_name, extra={"observability_fields": safe})

    def alert(self, **attributes: ObservationScalar) -> AlertFact:
        """构造受控告警事实；实际路由由后续部署环境选择，不在此发送外部消息。"""

        base = {"service": self.service_name, "environment": self.environment, **attributes}
        return AlertFact(self.fields.validate("alert", base))

    def record_authorization_decision(
        self,
        *,
        surface: str,
        outcome: str,
        reason_code: str,
    ) -> None:
        """记录低基数授权结果；权限码和目标资源只保留在受控业务事实中。"""

        labels = {
            **self.common_labels,
            "surface": surface,
            "outcome": outcome,
            "reason_code": reason_code,
        }
        self.fields.validate("metric", labels)
        self.authorization_decisions.labels(**labels).inc()
        self.log(
            "authorization_decision_completed",
            component="authorization",
            operation="decide",
            surface=surface,
            outcome=outcome,
            reason_code=reason_code,
        )

    def record_authorization_cache(self, cache_status: str) -> None:
        """记录策略版本见证状态；异常状态由 Prometheus 规则触发受控告警。"""

        labels = {**self.common_labels, "cache_status": cache_status}
        self.fields.validate("metric", labels)
        self.authorization_policy_cache.labels(**labels).inc()
        if cache_status not in {"fresh", "bootstrapped"}:
            self.log(
                "authorization_policy_cache_rejected",
                component="authorization",
                operation="verify_policy_version",
                cache_status=cache_status,
                outcome="denied",
            )

    def metrics_payload(self) -> bytes:
        """生成当前进程 Prometheus 文本，不暴露 Trace 或业务资源标识。"""

        if self._multiprocess_path is not None:
            # 抓取时重新聚合共享目录，API 因此能看见五个 Worker 子进程的指标。
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(  # type: ignore[no-untyped-call]
                registry,
                path=self._multiprocess_path,
            )
            return generate_latest(registry)
        return generate_latest(self.registry)

    def shutdown(self) -> None:
        """刷新并关闭 Span Processor，避免正常退出时丢失已结束 Span。"""

        self._provider.shutdown()
        if self._multiprocess_path is not None:
            process_identifier = f"{self._metrics_process_prefix}-{os.getpid()}"
            multiprocess.mark_process_dead(  # type: ignore[no-untyped-call]
                process_identifier,
                path=self._multiprocess_path,
            )


def bind_observability_runtime(
    runtime: ObservabilityRuntime,
    *,
    request_id: UUID | None = None,
    task_id: UUID | None = None,
) -> Token[_BoundContext | None]:
    """为框架边缘绑定运行时，业务模块通过 ContextVar 获取当前实例。"""

    return runtime.bind(request_id=request_id, task_id=task_id)


def current_observability_runtime() -> ObservabilityRuntime | None:
    """返回当前请求或任务绑定的运行时；离线领域测试可以不装配可观测设施。"""

    bound = _BOUND_CONTEXT.get()
    return None if bound is None else bound.runtime


def observed_operation(
    *,
    component: str,
    operation: str,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """为同步应用入口增加可选观测；未装配运行时时保持原有离线语义。"""

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            runtime = current_observability_runtime()
            if runtime is None:
                return function(*args, **kwargs)
            with runtime.operation(component=component, operation=operation):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def _otel_parent_context(parent: TraceContext) -> Context:
    span_context = SpanContext(
        trace_id=int(parent.trace_id, 16),
        span_id=int(parent.span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(int(parent.trace_flags, 16) & 1),
        trace_state=TraceState(),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context))


def trace_context_from_span(span: Span) -> TraceContext:
    """把 SDK Span 转回平台既有 W3C 事实，保证响应与业务记录使用同一标识。"""

    context = span.get_span_context()
    return TraceContext(
        trace_id=trace.format_trace_id(context.trace_id),
        span_id=trace.format_span_id(context.span_id),
        trace_flags=f"{int(context.trace_flags):02x}",
    )


def _stable_error_code(error: Exception) -> str:
    value = getattr(error, "error_code", None)
    if isinstance(value, str) and value:
        return value[:128]
    return type(error).__name__.upper()[:128]
