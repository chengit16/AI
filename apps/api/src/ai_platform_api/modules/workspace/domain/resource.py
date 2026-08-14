"""定义可字段掩码的空间资源和范围化 Repository 端口。"""

from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class WorkspaceResource:
    """保存归属于唯一工作空间的资源标识、名称和创建时间。"""

    resource_id: UUID
    workspace_id: UUID
    title: str
    sensitive_value: str | None
    version: int = 1

    def mask(self, fields: frozenset[str]) -> "WorkspaceResource":
        if "sensitive_value" in fields:
            return replace(self, sensitive_value=None)
        return self


class WorkspaceResourceRepository(Protocol):
    """只允许按工作空间写入和读取资源，跨空间标识不得返回实体。"""

    def add(self, resource: WorkspaceResource) -> None: ...

    def get_scoped(self, workspace_id: UUID, resource_id: UUID) -> WorkspaceResource | None: ...
