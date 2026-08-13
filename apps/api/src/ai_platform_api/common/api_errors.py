from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    message: str
    retryable: bool
    request_id: UUID
    trace_id: str = Field(min_length=16, max_length=64)


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        400: "请求上下文无效",
        401: "身份凭证无效",
        403: "请求未获授权",
        404: "资源不存在或不可见",
        409: "资源状态冲突",
        422: "请求参数无效",
        500: "平台内部错误",
    }
    return {
        status: {
            "model": ErrorResponse,
            "description": descriptions[status],
        }
        for status in statuses
    }
