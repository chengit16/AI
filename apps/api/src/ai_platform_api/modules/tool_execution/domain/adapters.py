"""定义内部只读工具 Adapter 的请求、结果和可替换调用端口。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ai_platform_api.common.request_context import RequestContext


@dataclass(frozen=True)
class ToolAdapterRequest:
    """携带可信上下文和已通过入口校验的工具参数。"""

    context: RequestContext
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ToolAdapterResult:
    """保存通过结果安全检查的最小工具结果及其内容摘要。"""

    tool_key: str
    tool_version: int
    payload: dict[str, object]
    output_schema_hash: str
    result_sha256: str
    result_size_bytes: int
    checks: tuple[str, ...]


class InternalReadAdapter(Protocol):
    """读取责任模块数据的封闭 Adapter，不允许携带凭证或任意外部地址。"""

    tool_key: str

    def execute(self, request: ToolAdapterRequest) -> Mapping[str, object]: ...
