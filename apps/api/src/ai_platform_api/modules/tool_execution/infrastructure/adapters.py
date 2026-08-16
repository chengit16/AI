"""提供五个内部只读工具的责任模块调用适配器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import ClassVar, cast

from ai_platform_api.modules.tool_execution.domain.adapters import (
    InternalReadAdapter,
    ToolAdapterRequest,
)

ToolHandler = Callable[[ToolAdapterRequest], Mapping[str, object]]


class _CallableInternalReadAdapter:
    """把责任模块的授权查询函数封装为不可访问网络的内部 Adapter。"""

    tool_key: ClassVar[str]

    def __init__(self, handler: ToolHandler) -> None:
        self._handler = handler

    def execute(self, request: ToolAdapterRequest) -> Mapping[str, object]:
        return self._handler(request)


class KnowledgeSearchAdapter(_CallableInternalReadAdapter):
    """承载 `knowledge.search` 的授权检索实现。"""

    tool_key = "knowledge.search"


class DocumentReadAuthorizedRangeAdapter(_CallableInternalReadAdapter):
    """承载 `document.read_authorized_range` 的授权片段读取实现。"""

    tool_key = "document.read_authorized_range"


class WorkflowGetStatusAdapter(_CallableInternalReadAdapter):
    """承载 `workflow.get_status` 的工作流状态读取实现。"""

    tool_key = "workflow.get_status"


class ApprovalGetStatusAdapter(_CallableInternalReadAdapter):
    """承载 `approval.get_status` 的审批状态读取实现。"""

    tool_key = "approval.get_status"


class QuotaGetUsageAdapter(_CallableInternalReadAdapter):
    """承载 `quota.get_usage` 的套餐用量读取实现。"""

    tool_key = "quota.get_usage"


def build_internal_read_adapters(
    *,
    knowledge_search: ToolHandler,
    document_read_authorized_range: ToolHandler,
    workflow_get_status: ToolHandler,
    approval_get_status: ToolHandler,
    quota_get_usage: ToolHandler,
) -> dict[str, InternalReadAdapter]:
    """由平台装配五个责任模块函数，返回不可扩展的工具键映射。"""

    return {
        KnowledgeSearchAdapter.tool_key: cast(
            InternalReadAdapter,
            KnowledgeSearchAdapter(knowledge_search),
        ),
        DocumentReadAuthorizedRangeAdapter.tool_key: cast(
            InternalReadAdapter,
            DocumentReadAuthorizedRangeAdapter(document_read_authorized_range),
        ),
        WorkflowGetStatusAdapter.tool_key: cast(
            InternalReadAdapter,
            WorkflowGetStatusAdapter(workflow_get_status),
        ),
        ApprovalGetStatusAdapter.tool_key: cast(
            InternalReadAdapter,
            ApprovalGetStatusAdapter(approval_get_status),
        ),
        QuotaGetUsageAdapter.tool_key: cast(
            InternalReadAdapter,
            QuotaGetUsageAdapter(quota_get_usage),
        ),
    }


__all__ = [
    "ApprovalGetStatusAdapter",
    "DocumentReadAuthorizedRangeAdapter",
    "KnowledgeSearchAdapter",
    "QuotaGetUsageAdapter",
    "ToolHandler",
    "WorkflowGetStatusAdapter",
    "build_internal_read_adapters",
]
