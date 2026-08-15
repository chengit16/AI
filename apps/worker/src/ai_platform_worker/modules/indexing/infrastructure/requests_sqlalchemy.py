"""实现索引维护请求的 PostgreSQL 租约和有限重试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal, cast

from ai_platform_backend.indexing.persistence import index_maintenance_requests
from sqlalchemy import CursorResult, and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_worker.modules.indexing.domain.commands import (
    ClaimedIndexMaintenanceRequest,
    IndexMaintenanceCommand,
)


class SqlAlchemyIndexMaintenanceRequestStore:
    """用数据库租约串行领取请求，并允许进程退出后从稳定运行 ID 恢复。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> tuple[ClaimedIndexMaintenanceRequest, ...]:
        claim_until = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session, session.begin():
            # 1. 达到上限的过期租约先进入稳定死信，避免 Worker 反复退出造成无限重试。
            session.execute(
                update(index_maintenance_requests)
                .where(
                    index_maintenance_requests.c.status == "running",
                    index_maintenance_requests.c.claim_until <= now,
                    index_maintenance_requests.c.attempt_count >= max_attempts,
                )
                .values(
                    status="dead_letter",
                    claimed_by=None,
                    claim_until=None,
                    last_error_code="WORKER_LEASE_EXPIRED",
                    updated_at=now,
                    completed_at=now,
                )
            )
            # 2. 使用跳锁领取到期请求，并在同一事务写入租约与递增后的尝试次数。
            rows = session.execute(
                select(index_maintenance_requests)
                .where(
                    and_(
                        or_(
                            index_maintenance_requests.c.status.in_(("pending", "retry_wait")),
                            (index_maintenance_requests.c.status == "running")
                            & (index_maintenance_requests.c.claim_until <= now),
                        ),
                        index_maintenance_requests.c.attempt_count < max_attempts,
                    )
                )
                .order_by(
                    index_maintenance_requests.c.created_at,
                    index_maintenance_requests.c.maintenance_request_id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).mappings()
            claimed: list[ClaimedIndexMaintenanceRequest] = []
            for row in rows:
                attempt_count = int(row["attempt_count"]) + 1
                session.execute(
                    update(index_maintenance_requests)
                    .where(
                        index_maintenance_requests.c.maintenance_request_id
                        == row["maintenance_request_id"]
                    )
                    .values(
                        status="running",
                        attempt_count=attempt_count,
                        claimed_by=worker_id,
                        claim_until=claim_until,
                        last_error_code=None,
                        updated_at=now,
                    )
                )
                claimed.append(
                    ClaimedIndexMaintenanceRequest(
                        maintenance_request_id=row["maintenance_request_id"],
                        workspace_id=row["workspace_id"],
                        command=cast(IndexMaintenanceCommand, row["command"]),
                        requested_by_actor_id=row["requested_by_actor_id"],
                        attempt_count=attempt_count,
                        claimed_by=worker_id,
                    )
                )
        return tuple(claimed)

    def mark_completed(
        self,
        request: ClaimedIndexMaintenanceRequest,
        *,
        completed_at: datetime,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(index_maintenance_requests)
                    .where(*_owned_claim(request))
                    .values(
                        status="completed",
                        claimed_by=None,
                        claim_until=None,
                        last_error_code=None,
                        updated_at=completed_at,
                        completed_at=completed_at,
                    )
                ),
            )
        return result.rowcount == 1

    def mark_failed(
        self,
        request: ClaimedIndexMaintenanceRequest,
        *,
        failed_at: datetime,
        error_code: str,
        max_attempts: int,
    ) -> Literal["retry_wait", "dead_letter", "lost_claim"]:
        status: Literal["retry_wait", "dead_letter"] = (
            "dead_letter" if request.attempt_count >= max_attempts else "retry_wait"
        )
        with self._session_factory() as session, session.begin():
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(index_maintenance_requests)
                    .where(*_owned_claim(request))
                    .values(
                        status=status,
                        claimed_by=None,
                        claim_until=None,
                        last_error_code=error_code,
                        updated_at=failed_at,
                        completed_at=failed_at if status == "dead_letter" else None,
                    )
                ),
            )
        return status if result.rowcount == 1 else "lost_claim"


def _owned_claim(request: ClaimedIndexMaintenanceRequest) -> tuple[Any, ...]:
    return (
        index_maintenance_requests.c.maintenance_request_id == request.maintenance_request_id,
        index_maintenance_requests.c.status == "running",
        index_maintenance_requests.c.claimed_by == request.claimed_by,
        index_maintenance_requests.c.attempt_count == request.attempt_count,
    )
