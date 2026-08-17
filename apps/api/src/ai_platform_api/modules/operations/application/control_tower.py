"""编排质量、成本、隔离、法规与私有实例控制台的只读查询。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.operations.domain.control_tower import (
    ControlTowerSnapshotSource,
    OperationsControlTowerSnapshot,
)

READ_PERMISSION = "operations.control_tower.read"


class OperationsControlTowerDeniedError(PlatformError):
    """当前主体没有目标工作空间的控制台读取权限。"""

    error_code = "POLICY_DENIED"


class OperationsControlTowerService:
    """在服务端重新核对可信空间和权限，禁止调用方伪造通过状态。"""

    def __init__(self, source: ControlTowerSnapshotSource) -> None:
        self._source = source

    def snapshot(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        now: datetime | None = None,
    ) -> OperationsControlTowerSnapshot:
        """返回不含业务正文、凭证和外部连接标识的当前控制台快照。"""

        if (
            context.workspace_id != workspace_id
            or context.authorized_permission_code != READ_PERMISSION
            or not context.authorized_workspace
        ):
            raise OperationsControlTowerDeniedError
        return self._source.snapshot(workspace_id, generated_at=now or datetime.now(UTC))
