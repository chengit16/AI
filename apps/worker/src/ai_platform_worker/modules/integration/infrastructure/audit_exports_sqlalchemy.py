"""实现审计导出请求的 PostgreSQL 租约和安全投影。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from ai_platform_backend.integration.persistence import (
    audit_export_requests,
    audit_records,
)
from sqlalchemy import CursorResult, and_, or_, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_worker.modules.integration.domain.audit_exports import (
    AuditExportResult,
    ClaimedAuditExportRequest,
)


class SqlAlchemyAuditExportRequestStore:
    """使用数据库租约处理导出，并只读取冻结空间与筛选范围。"""

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
    ) -> tuple[ClaimedAuditExportRequest, ...]:
        claim_until = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session, session.begin():
            # 1. 达到上限的过期租约先进入死信，避免进程退出造成无限重放。
            session.execute(
                update(audit_export_requests)
                .where(
                    audit_export_requests.c.status == "running",
                    audit_export_requests.c.claim_until <= now,
                    audit_export_requests.c.attempt_count >= max_attempts,
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
            # 2. 使用 SKIP LOCKED 有界领取可执行请求，并在同一事务内写入新租约和尝试次数。
            rows = session.execute(
                select(audit_export_requests)
                .where(
                    and_(
                        or_(
                            audit_export_requests.c.status.in_(("pending", "retry_wait")),
                            (audit_export_requests.c.status == "running")
                            & (audit_export_requests.c.claim_until <= now),
                        ),
                        audit_export_requests.c.attempt_count < max_attempts,
                    )
                )
                .order_by(
                    audit_export_requests.c.created_at,
                    audit_export_requests.c.audit_export_request_id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).mappings()
            claimed: list[ClaimedAuditExportRequest] = []
            for row in rows:
                attempt_count = int(row["attempt_count"]) + 1
                session.execute(
                    update(audit_export_requests)
                    .where(
                        audit_export_requests.c.audit_export_request_id
                        == row["audit_export_request_id"]
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
                claimed.append(_claimed_request(row, attempt_count, worker_id))
        return tuple(claimed)

    def render_safe_result(self, request: ClaimedAuditExportRequest) -> AuditExportResult:
        """生成确定性 JSONL 哈希；自由属性和受遮罩主体不进入投影。"""

        with self._session_factory() as session:
            # 1. 查询始终绑定冻结空间、时间窗和筛选，只选择允许导出的固定标量列。
            statement = select(
                audit_records.c.audit_id,
                audit_records.c.actor_id,
                audit_records.c.user_id,
                audit_records.c.action,
                audit_records.c.resource_type,
                audit_records.c.resource_id,
                audit_records.c.outcome,
                audit_records.c.occurred_at,
                audit_records.c.request_id,
                audit_records.c.trace_id,
                audit_records.c.permission_code,
            ).where(
                audit_records.c.workspace_id == request.workspace_id,
                audit_records.c.occurred_at < request.occurred_to,
            )
            if request.occurred_from is not None:
                statement = statement.where(audit_records.c.occurred_at >= request.occurred_from)
            if request.actor_id is not None:
                statement = statement.where(audit_records.c.actor_id == request.actor_id)
            if request.action is not None:
                statement = statement.where(audit_records.c.action == request.action)
            if request.resource_type is not None:
                statement = statement.where(audit_records.c.resource_type == request.resource_type)
            if request.outcome is not None:
                statement = statement.where(audit_records.c.outcome == request.outcome)
            rows = session.execute(
                statement.order_by(audit_records.c.occurred_at, audit_records.c.audit_id)
            ).mappings()
            digest = hashlib.sha256()
            row_count = 0
            # 2. 按稳定顺序应用字段遮罩并计算确定性摘要，不持久化原始内容或对象定位。
            for row in rows:
                projected = _safe_projection(row, request.field_mask)
                digest.update(
                    json.dumps(
                        projected,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                digest.update(b"\n")
                row_count += 1
        return AuditExportResult(
            row_count=row_count,
            sha256=digest.hexdigest(),
            summary=f"已生成 {row_count} 条脱敏审计记录的安全摘要",
        )

    def mark_completed(
        self,
        request: ClaimedAuditExportRequest,
        *,
        result: AuditExportResult,
        completed_at: datetime,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            outcome = cast(
                CursorResult[Any],
                session.execute(
                    update(audit_export_requests)
                    .where(*_owned_claim(request))
                    .values(
                        status="completed",
                        claimed_by=None,
                        claim_until=None,
                        last_error_code=None,
                        row_count=result.row_count,
                        result_sha256=result.sha256,
                        result_summary=result.summary,
                        updated_at=completed_at,
                        completed_at=completed_at,
                    )
                ),
            )
        return outcome.rowcount == 1

    def mark_failed(
        self,
        request: ClaimedAuditExportRequest,
        *,
        failed_at: datetime,
        error_code: str,
        max_attempts: int,
    ) -> Literal["retry_wait", "dead_letter", "lost_claim"]:
        status: Literal["retry_wait", "dead_letter"] = (
            "dead_letter" if request.attempt_count >= max_attempts else "retry_wait"
        )
        with self._session_factory() as session, session.begin():
            outcome = cast(
                CursorResult[Any],
                session.execute(
                    update(audit_export_requests)
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
        return status if outcome.rowcount == 1 else "lost_claim"


def _claimed_request(
    row: RowMapping, attempt_count: int, worker_id: str
) -> ClaimedAuditExportRequest:
    # 未显式提供时间上界时使用创建时刻，确保异步执行不会纳入后来产生的记录。
    occurred_to = cast(datetime, row["occurred_to"] or row["created_at"])
    return ClaimedAuditExportRequest(
        audit_export_request_id=row["audit_export_request_id"],
        workspace_id=row["workspace_id"],
        actor_id=row["actor_id"],
        action=row["action"],
        resource_type=row["resource_type"],
        outcome=cast(Literal["succeeded", "denied", "failed"] | None, row["outcome"]),
        occurred_from=row["occurred_from"],
        occurred_to=occurred_to,
        field_mask=frozenset(row["field_mask"]),
        attempt_count=attempt_count,
        claimed_by=worker_id,
    )


def _safe_projection(row: RowMapping, field_mask: frozenset[str]) -> dict[str, object]:
    """只输出固定标量列，字段策略可继续遮罩主体标识。"""

    return {
        "audit_id": str(row["audit_id"]),
        "actor_id": None if "actor_id" in field_mask else str(row["actor_id"]),
        "user_id": (
            None if "user_id" in field_mask or row["user_id"] is None else str(row["user_id"])
        ),
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": str(row["resource_id"]),
        "outcome": row["outcome"],
        "occurred_at": row["occurred_at"].isoformat(),
        "request_id": str(row["request_id"]),
        "trace_id": row["trace_id"],
        "permission_code": row["permission_code"],
    }


def _owned_claim(request: ClaimedAuditExportRequest) -> tuple[Any, ...]:
    return (
        audit_export_requests.c.audit_export_request_id == request.audit_export_request_id,
        audit_export_requests.c.status == "running",
        audit_export_requests.c.claimed_by == request.claimed_by,
        audit_export_requests.c.attempt_count == request.attempt_count,
    )
