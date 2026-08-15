"""编排工作空间审计、Outbox 重放与消费者幂等运营用例。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.integration.domain.operations import (
    AuditOperationsPage,
    AuditOperationsRecord,
    AuditOutcome,
    IntegrationInspection,
    IntegrationOperationsCursorError,
    IntegrationOperationsUnitOfWork,
    IntegrationOperationsWriteConflictError,
    OutboxOperationsPage,
    OutboxOperationsRecord,
    OutboxReplayRequest,
    OutboxStatus,
)

__all__ = [
    "AuditOperationsPage",
    "AuditOperationsRecord",
    "AuditOutcome",
    "IntegrationInspection",
    "IntegrationOperationsService",
    "OutboxOperationsPage",
    "OutboxOperationsRecord",
    "OutboxReplayRequest",
    "OutboxStatus",
]

READ_PERMISSION = "operations.records.read"
REPLAY_PERMISSION = "operations.outbox.replay"
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$")


class OperationsDeniedError(PlatformError):
    """当前主体没有工作空间运营读取或重放权限。"""

    error_code = "POLICY_DENIED"


class OperationsNotFoundError(PlatformError):
    """运营资源不存在或不属于当前工作空间。"""

    error_code = "RESOURCE_NOT_FOUND"


class OperationsValidationError(PlatformError):
    """运营筛选、游标或重放参数不符合稳定契约。"""

    error_code = "VALIDATION_ERROR"


class OperationsConflictError(PlatformError):
    """Outbox 当前状态、Schema 或并发版本不允许重放。"""

    error_code = "OPERATIONS_CONFLICT"


class OperationsIdempotencyConflictError(PlatformError):
    """重放幂等键已经绑定到另一份请求。"""

    error_code = "IDEMPOTENCY_CONFLICT"


class IntegrationOperationsService:
    """集中执行工作空间运营查询和保持事件 ID 不变的受控重放。"""

    def __init__(self, unit_of_work: IntegrationOperationsUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def list_audit_records(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        limit: int,
        cursor: UUID | None = None,
        actor_id: UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        outcome: AuditOutcome | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> AuditOperationsPage:
        """按可信空间和可选筛选返回不可变审计事实。"""

        self._require(context, workspace_id, READ_PERMISSION)
        _validate_window(limit, occurred_from, occurred_to)
        try:
            with self._unit_of_work as unit_of_work:
                return unit_of_work.operations.list_audit_records(
                    workspace_id,
                    limit=limit,
                    cursor=cursor,
                    actor_id=actor_id,
                    action=_optional_filter(action),
                    resource_type=_optional_filter(resource_type),
                    outcome=outcome,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
        except IntegrationOperationsCursorError as error:
            raise OperationsValidationError from error

    def list_outbox_events(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        limit: int,
        cursor: UUID | None = None,
        status: OutboxStatus | None = None,
        event_type: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> OutboxOperationsPage:
        """返回不含 Payload 的 Outbox 状态与追踪元数据。"""

        self._require(context, workspace_id, READ_PERMISSION)
        _validate_window(limit, occurred_from, occurred_to)
        normalized_event_type = _optional_filter(event_type)
        if normalized_event_type is not None and not EVENT_TYPE_PATTERN.fullmatch(
            normalized_event_type
        ):
            raise OperationsValidationError
        try:
            with self._unit_of_work as unit_of_work:
                return unit_of_work.operations.list_outbox_events(
                    workspace_id,
                    limit=limit,
                    cursor=cursor,
                    status=status,
                    event_type=normalized_event_type,
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                )
        except IntegrationOperationsCursorError as error:
            raise OperationsValidationError from error

    def inspect(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        now: datetime | None = None,
    ) -> IntegrationInspection:
        """检查 Outbox 积压、V1 Schema 与消费者回执结构完整性。"""

        self._require(context, workspace_id, READ_PERMISSION)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.operations.inspect(workspace_id, now=now or datetime.now(UTC))

    def replay_outbox_event(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        event_id: UUID,
        idempotency_key: str,
        reason_code: str,
        requested_at: datetime | None = None,
    ) -> OutboxReplayRequest:
        """保留原事件 ID 重排已发布或死信事件，使消费者继续按回执判重。"""

        # 1. 重放只接受已授权浏览器主体和结构化原因，禁止 API Key 静默触发高风险操作。
        self._require(context, workspace_id, REPLAY_PERMISSION)
        if context.authentication_method != "browser_session" or context.user_id is None:
            raise OperationsDeniedError
        if (
            IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None
            or REASON_PATTERN.fullmatch(reason_code) is None
        ):
            raise OperationsValidationError
        now = requested_at or datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 同一幂等键只能重放同一事件和原因；重复请求返回首次不可变事实。
                previous = unit_of_work.operations.get_replay_request(
                    workspace_id,
                    idempotency_key,
                )
                if previous is not None:
                    if previous.event_id != event_id or previous.reason_code != reason_code:
                        raise OperationsIdempotencyConflictError
                    return previous
                source = unit_of_work.operations.get_replay_source(workspace_id, event_id)
                if source is None:
                    raise OperationsNotFoundError
                if source.status not in {"published", "dead_letter"}:
                    raise OperationsConflictError
                if (
                    source.schema_version != 1
                    or EVENT_TYPE_PATTERN.fullmatch(source.event_type) is None
                ):
                    raise OperationsConflictError
                source_status = cast(
                    Literal["published", "dead_letter"],
                    source.status,
                )
                request = OutboxReplayRequest(
                    replay_request_id=uuid4(),
                    workspace_id=workspace_id,
                    event_id=event_id,
                    idempotency_key=idempotency_key,
                    reason_code=reason_code,
                    source_status=source_status,
                    source_attempt_count=source.attempt_count,
                    source_published_at=source.published_at,
                    source_error_code=source.last_error_code,
                    requested_by_actor_id=context.actor_id,
                    requested_by_user_id=context.user_id,
                    request_id=context.request_id,
                    trace_id=context.trace.trace_id,
                    traceparent=context.trace.traceparent,
                    requested_at=now,
                )
                # 3. 请求事实、原事件重排和审计同事务提交；不递归产生新的 Outbox 事件。
                unit_of_work.operations.add_replay_request(request)
                if not unit_of_work.operations.requeue_event(
                    workspace_id=workspace_id,
                    event_id=event_id,
                    source_status=source_status,
                    available_at=now,
                ):
                    raise OperationsConflictError
                unit_of_work.audit.add(
                    AuditRecord(
                        audit_id=uuid4(),
                        workspace_id=workspace_id,
                        actor_id=context.actor_id,
                        user_id=context.user_id,
                        action="operations.outbox.replay",
                        resource_type="outbox_event",
                        resource_id=event_id,
                        outcome="succeeded",
                        occurred_at=now,
                        request_id=context.request_id,
                        trace_id=context.trace.trace_id,
                        traceparent=context.trace.traceparent,
                        authorization=context.audit_authorization,
                        attributes={
                            "replay_request_id": str(request.replay_request_id),
                            "source_status": source.status,
                            "source_attempt_count": source.attempt_count,
                            "reason_code": reason_code,
                        },
                    )
                )
                unit_of_work.commit()
                return request
        except IntegrationOperationsWriteConflictError as error:
            # 并发相同请求可能先由另一事务写入唯一键；提交失败后重新读取首次事实。
            with self._unit_of_work as unit_of_work:
                previous = unit_of_work.operations.get_replay_request(
                    workspace_id,
                    idempotency_key,
                )
                if previous is None:
                    raise OperationsConflictError from error
                if previous.event_id != event_id or previous.reason_code != reason_code:
                    raise OperationsIdempotencyConflictError from error
                return previous

    @staticmethod
    def _require(context: RequestContext, workspace_id: UUID, permission_code: str) -> None:
        if (
            context.workspace_id != workspace_id
            or context.authorized_permission_code != permission_code
            or not context.authorized_workspace
        ):
            raise OperationsDeniedError


def _validate_window(
    limit: int,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
) -> None:
    if limit < 1 or limit > 200:
        raise OperationsValidationError
    if occurred_from is not None and occurred_from.tzinfo is None:
        raise OperationsValidationError
    if occurred_to is not None and occurred_to.tzinfo is None:
        raise OperationsValidationError
    if occurred_from is not None and occurred_to is not None and occurred_from >= occurred_to:
        raise OperationsValidationError


def _optional_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise OperationsValidationError
    return normalized
