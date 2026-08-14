"""工作空间隔离资源与持久化端口的公开入口。"""

from ai_platform_api.modules.workspace.domain.resource import (
    WorkspaceResource,
    WorkspaceResourceRepository,
)

__all__ = ["WorkspaceResource", "WorkspaceResourceRepository"]
