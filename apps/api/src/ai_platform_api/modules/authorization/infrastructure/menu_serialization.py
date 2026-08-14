"""在菜单领域对象与 PostgreSQL 行之间执行集中序列化。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy.engine import Row

from ai_platform_api.modules.authorization.domain.menu_documents import (
    menu_snapshot_from_dict,
    menu_snapshot_to_dict,
)
from ai_platform_api.modules.authorization.domain.menus import (
    MenuRelease,
    MenuReleaseKind,
    MenuReleaseStatus,
)


def menu_release_values(release: MenuRelease) -> dict[str, object]:
    """将菜单发布聚合映射为数据库写入值，保持快照与摘要一致。"""

    return {
        "release_id": release.release_id,
        "workspace_id": release.workspace_id,
        "release_number": release.release_number,
        "release_kind": release.release_kind,
        "source_release_id": release.source_release_id,
        "status": release.status,
        "snapshot": menu_snapshot_to_dict(release.snapshot),
        "snapshot_digest": release.snapshot_digest,
        "validation_errors": list(release.validation_errors),
        "rejection_reason": release.rejection_reason,
        "created_by_account_id": release.created_by_account_id,
        "decided_by_account_id": release.decided_by_account_id,
        "created_at": release.created_at,
        "validated_at": release.validated_at,
        "decided_at": release.decided_at,
        "published_at": release.published_at,
        "version": release.version,
    }


def menu_release_from_row(row: Row[Any]) -> MenuRelease:
    """从数据库行恢复不可变菜单发布聚合。"""

    return MenuRelease(
        row.release_id,
        row.workspace_id,
        row.release_number,
        cast("MenuReleaseKind", row.release_kind),
        row.source_release_id,
        cast("MenuReleaseStatus", row.status),
        menu_snapshot_from_dict(cast(dict[str, Any], row.snapshot)),
        row.snapshot_digest,
        tuple(row.validation_errors),
        row.rejection_reason,
        row.created_by_account_id,
        row.decided_by_account_id,
        cast(datetime, row.created_at),
        row.validated_at,
        row.decided_at,
        row.published_at,
        row.version,
    )
