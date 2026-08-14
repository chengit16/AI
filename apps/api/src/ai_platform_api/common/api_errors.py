"""定义统一错误响应 Schema 和 Router 可复用的错误状态声明。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    """表示错误响应，由协议层映射为稳定错误码。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    message: str
    retryable: bool
    request_id: UUID
    trace_id: str = Field(min_length=16, max_length=64)


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """将业务错误定义转换为 OpenAPI 可声明的稳定响应集合。"""

    descriptions = {
        400: "请求上下文无效",
        401: "身份凭证无效",
        403: "请求未获授权",
        404: "资源不存在或不可见",
        409: "资源状态冲突",
        410: "请求的恢复事实已过期",
        422: "请求参数无效",
        500: "平台内部错误",
        503: "依赖服务暂时不可用",
        413: "上传内容超过限制",
        415: "上传内容类型不受支持",
    }
    return {
        status: {
            "model": ErrorResponse,
            "description": descriptions[status],
        }
        for status in statuses
    }
