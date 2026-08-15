"""提供索引版本原子切换、停用和发布指针查询 SQL 操作。"""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from ai_platform_backend.indexing.facts import document_publications
from ai_platform_backend.indexing.persistence import (
    document_index_publications,
    index_versions,
    retrieval_chunks,
)


def switch_active_document_index(
    session: Session,
    *,
    workspace_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    activated_at: datetime,
) -> UUID | None:
    """在一个事务中撤销旧索引并启用目标文档版本的最新可用索引。"""

    # 1. 发布事实始终先写入；锁定发布指针并选择最新 ready 版本以串行化并发重建。
    session.execute(
        select(document_publications.c.current_document_version_id)
        .where(
            document_publications.c.workspace_id == workspace_id,
            document_publications.c.document_id == document_id,
        )
        .with_for_update()
    ).one_or_none()
    candidate = session.execute(
        select(index_versions.c.index_version_id)
        .where(
            index_versions.c.workspace_id == workspace_id,
            index_versions.c.document_id == document_id,
            index_versions.c.document_version_id == document_version_id,
            index_versions.c.status.in_(("ready", "active")),
        )
        .order_by(index_versions.c.build_no.desc(), index_versions.c.completed_at.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()

    # 2. 即使目标版本尚无索引，也必须先撤销旧版本，不能继续召回已过期正文。
    _clear_document_index_activation(
        session,
        workspace_id=workspace_id,
        document_id=document_id,
        changed_at=activated_at,
    )
    if candidate is None:
        return None

    # 3. 目标版本、Chunk 和当前索引指针在同一事务中启用，对检索端一次可见。
    _activate_document_index_version_locked(
        session,
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=document_version_id,
        index_version_id=candidate,
        activated_at=activated_at,
    )
    return cast(UUID, candidate)


def activate_document_index_version(
    session: Session,
    *,
    workspace_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    index_version_id: UUID,
    activated_at: datetime,
) -> bool:
    """原子启用巡检已验证完整的指定索引版本，不自行选择其他候选。"""

    # 1. 当前业务发布版本拥有优先写入权；巡检不能把历史文档版本重新暴露。
    current_version_id = session.execute(
        select(document_publications.c.current_document_version_id)
        .where(
            document_publications.c.workspace_id == workspace_id,
            document_publications.c.document_id == document_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if current_version_id != document_version_id:
        return False
    candidate = session.execute(
        select(index_versions.c.index_version_id)
        .where(
            index_versions.c.workspace_id == workspace_id,
            index_versions.c.document_id == document_id,
            index_versions.c.document_version_id == document_version_id,
            index_versions.c.index_version_id == index_version_id,
            index_versions.c.status.in_(("ready", "active", "retired")),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if candidate is None:
        return False
    # 2. 指定候选通过上层完整性校验后，清理旧活动面并在同一事务切换全部引用。
    _clear_document_index_activation(
        session,
        workspace_id=workspace_id,
        document_id=document_id,
        changed_at=activated_at,
    )
    _activate_document_index_version_locked(
        session,
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=document_version_id,
        index_version_id=cast(UUID, candidate),
        activated_at=activated_at,
    )
    return True


def clear_document_index_activation(
    session: Session,
    *,
    workspace_id: UUID,
    document_id: UUID,
    changed_at: datetime,
) -> None:
    """停用异常派生索引和发布指针，但保留仍可恢复的在途构建。"""

    _clear_document_index_activation(
        session,
        workspace_id=workspace_id,
        document_id=document_id,
        changed_at=changed_at,
    )


def _clear_document_index_activation(
    session: Session,
    *,
    workspace_id: UUID,
    document_id: UUID,
    changed_at: datetime,
) -> None:
    session.execute(
        update(retrieval_chunks)
        .where(
            retrieval_chunks.c.workspace_id == workspace_id,
            retrieval_chunks.c.document_id == document_id,
            retrieval_chunks.c.active.is_(True),
        )
        .values(active=False)
    )
    session.execute(
        update(index_versions)
        .where(
            index_versions.c.workspace_id == workspace_id,
            index_versions.c.document_id == document_id,
            index_versions.c.status == "active",
        )
        .values(status="retired", updated_at=changed_at)
    )
    session.execute(
        delete(document_index_publications).where(
            document_index_publications.c.workspace_id == workspace_id,
            document_index_publications.c.document_id == document_id,
        )
    )


def _activate_document_index_version_locked(
    session: Session,
    *,
    workspace_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    index_version_id: UUID,
    activated_at: datetime,
) -> None:
    session.execute(
        update(index_versions)
        .where(index_versions.c.index_version_id == index_version_id)
        .values(status="active", activated_at=activated_at, updated_at=activated_at)
    )
    session.execute(
        update(retrieval_chunks)
        .where(
            retrieval_chunks.c.index_version_id == index_version_id,
            retrieval_chunks.c.workspace_id == workspace_id,
            retrieval_chunks.c.document_id == document_id,
        )
        .values(active=True)
    )
    statement = postgresql_insert(document_index_publications).values(
        workspace_id=workspace_id,
        document_id=document_id,
        document_version_id=document_version_id,
        index_version_id=index_version_id,
        activated_at=activated_at,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                document_index_publications.c.workspace_id,
                document_index_publications.c.document_id,
            ],
            set_={
                "document_version_id": statement.excluded.document_version_id,
                "index_version_id": statement.excluded.index_version_id,
                "activated_at": statement.excluded.activated_at,
            },
        )
    )


def deactivate_document_indexes(
    session: Session,
    *,
    workspace_id: UUID,
    document_id: UUID,
    deactivated_at: datetime,
) -> None:
    """文档撤权时同时停用已发布 Chunk 并终止尚未完成的构建。"""

    # 1. 先停用全部可检索 Chunk 和 ready/active 版本，使撤权立即对读取端生效。
    session.execute(
        update(retrieval_chunks)
        .where(
            retrieval_chunks.c.workspace_id == workspace_id,
            retrieval_chunks.c.document_id == document_id,
        )
        .values(active=False)
    )
    session.execute(
        update(index_versions)
        .where(
            index_versions.c.workspace_id == workspace_id,
            index_versions.c.document_id == document_id,
            index_versions.c.status.in_(("ready", "active")),
        )
        .values(status="retired", updated_at=deactivated_at)
    )
    # 2. 再终止排队或运行中的构建并删除发布指针，后台任务不能重新暴露该文档。
    session.execute(
        update(index_versions)
        .where(
            index_versions.c.workspace_id == workspace_id,
            index_versions.c.document_id == document_id,
            index_versions.c.status.in_(
                (
                    "queued",
                    "embedding_running",
                    "embedding_retry_wait",
                    "index_queued",
                    "index_running",
                    "index_retry_wait",
                )
            ),
        )
        .values(
            status="failed",
            claimed_by=None,
            claim_until=None,
            active_attempt_id=None,
            completed_at=deactivated_at,
            staged_chunk_count=None,
            failure_stage="index",
            error_code="INDEX_DOCUMENT_REVOKED",
            error_message="文档已撤权: 索引构建已终止",
            updated_at=deactivated_at,
        )
    )
    session.execute(
        delete(document_index_publications).where(
            document_index_publications.c.workspace_id == workspace_id,
            document_index_publications.c.document_id == document_id,
        )
    )


def published_document_version_id(
    session: Session,
    *,
    workspace_id: UUID,
    document_id: UUID,
) -> UUID | None:
    """处理已发布文档版本标识，并保持调用方可依赖的稳定返回语义。"""

    value = session.execute(
        select(document_publications.c.current_document_version_id).where(
            document_publications.c.workspace_id == workspace_id,
            document_publications.c.document_id == document_id,
        )
    ).scalar_one_or_none()
    return cast(UUID | None, value)
