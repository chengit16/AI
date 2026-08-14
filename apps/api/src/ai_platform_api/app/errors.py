"""把领域稳定错误映射为脱敏 HTTP 响应和可追踪错误码。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHttpException

from ai_platform_api.common.api_errors import ErrorResponse
from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.trace import TraceContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErrorDefinition:
    """表示错误定义，由协议层映射为稳定错误码。"""

    code: str
    http_status: int
    message: str
    retryable: bool


class ErrorCatalog:
    """从版本化契约加载错误定义，避免 HTTP 边缘复制状态码与公开文案。"""

    def __init__(self, definitions: dict[str, ErrorDefinition]) -> None:
        if "INTERNAL_ERROR" not in definitions:
            raise ValueError("错误目录必须包含 INTERNAL_ERROR")
        self._definitions = definitions

    @classmethod
    def load(cls, path: str | Path) -> ErrorCatalog:
        catalog_path = Path(path)
        try:
            document = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"无法加载错误目录: {catalog_path}") from error
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("错误目录版本无效")
        entries = document.get("errors")
        if not isinstance(entries, list):
            raise ValueError("错误目录缺少 errors 列表")

        definitions: dict[str, ErrorDefinition] = {}
        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                raise ValueError("错误目录条目必须是对象")
            code = raw_entry.get("code")
            status = raw_entry.get("http_status")
            message = raw_entry.get("message")
            retryable = raw_entry.get("retryable")
            if (
                not isinstance(code, str)
                or not isinstance(status, int)
                or not isinstance(message, str)
                or not isinstance(retryable, bool)
            ):
                raise ValueError("错误目录条目字段类型无效")
            if code in definitions:
                raise ValueError(f"错误目录包含重复错误码: {code}")
            definitions[code] = ErrorDefinition(code, status, message, retryable)
        return cls(definitions)

    def resolve(self, code: str) -> ErrorDefinition:
        return self._definitions.get(code, self._definitions["INTERNAL_ERROR"])


def _request_identity(request: Request) -> tuple[UUID, str]:
    state = request.scope.get("state", {})
    request_id = state.get("request_id")
    trace = state.get("trace_context")
    trusted_request_id = request_id if isinstance(request_id, UUID) else uuid4()
    trusted_trace = trace if isinstance(trace, TraceContext) else TraceContext.new()
    return trusted_request_id, trusted_trace.trace_id


def _error_response(request: Request, catalog: ErrorCatalog, code: str) -> JSONResponse:
    definition = catalog.resolve(code)
    request_id, trace_id = _request_identity(request)
    body = ErrorResponse(
        code=definition.code,
        message=definition.message,
        retryable=definition.retryable,
        request_id=request_id,
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=definition.http_status,
        content=body.model_dump(mode="json"),
    )


def register_error_handlers(application: FastAPI, catalog: ErrorCatalog) -> None:
    """在协议边缘统一隐藏内部异常，稳定错误响应只取自版本化目录。"""

    async def handle_platform_error(request: Request, error: Exception) -> JSONResponse:
        platform_error = cast(PlatformError, error)
        return _error_response(request, catalog, platform_error.error_code)

    async def handle_validation_error(request: Request, error: Exception) -> JSONResponse:
        if not isinstance(error, RequestValidationError):
            raise TypeError("验证异常处理器收到错误类型")
        return _error_response(request, catalog, "VALIDATION_ERROR")

    async def handle_http_error(request: Request, error: Exception) -> JSONResponse:
        http_error = cast(StarletteHttpException, error)
        code_by_status = {
            401: "AUTH_REQUIRED",
            403: "POLICY_DENIED",
            404: "RESOURCE_NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            413: "INGESTION_FILE_TOO_LARGE",
            415: "INGESTION_UNSUPPORTED_FORMAT",
            422: "VALIDATION_ERROR",
        }
        return _error_response(
            request,
            catalog,
            code_by_status.get(http_error.status_code, "INTERNAL_ERROR"),
        )

    async def handle_unknown_error(request: Request, error: Exception) -> JSONResponse:
        request_id, trace_id = _request_identity(request)
        # 未知异常文本可能包含数据库值或供应商响应，只记录类型与可信关联标识。
        logger.error(
            "未处理的平台异常 type=%s request_id=%s trace_id=%s",
            type(error).__name__,
            request_id,
            trace_id,
        )
        return _error_response(request, catalog, "INTERNAL_ERROR")

    application.add_exception_handler(PlatformError, handle_platform_error)
    application.add_exception_handler(RequestValidationError, handle_validation_error)
    application.add_exception_handler(StarletteHttpException, handle_http_error)
    application.add_exception_handler(Exception, handle_unknown_error)
