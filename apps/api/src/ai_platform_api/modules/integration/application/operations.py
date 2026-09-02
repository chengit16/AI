"""编排工作空间审计、Outbox 重放与消费者幂等运营用例。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.integration.domain.operations import (
    AuditExportRequest,
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
    "AuditExportRequest",
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
SENSITIVE_AUDIT_KEY_PATTERN = re.compile(
    r"(?:password|token|secret|cookie|object_key|content|payload|credential|authorization)",
    re.IGNORECASE,
)


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
                page = unit_of_work.operations.list_audit_records(
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
                return AuditOperationsPage(
                    items=tuple(
                        _project_audit_record(
                            item, context.authorized_field_mask, include_attributes=False
                        )
                        for item in page.items
                    ),
                    next_cursor=page.next_cursor,
                )
        except IntegrationOperationsCursorError as error:
            raise OperationsValidationError from error

    def get_audit_record(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        audit_id: UUID,
    ) -> AuditOperationsRecord:
        """返回白名单化详情；自由属性不会未经裁剪越过审计边界。"""

        self._require(context, workspace_id, READ_PERMISSION)
        with self._unit_of_work as unit_of_work:
            record = unit_of_work.operations.get_audit_record(workspace_id, audit_id)
        if record is None:
            raise OperationsNotFoundError
        return _project_audit_record(
            record,
            context.authorized_field_mask,
            include_attributes=True,
        )

    def list_audit_export_requests(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[AuditExportRequest, ...]:
        """返回当前工作空间的导出状态，不返回内部哈希和传播字段。"""

        self._require(context, workspace_id, READ_PERMISSION)
        if limit < 1 or limit > 100:
            raise OperationsValidationError
        with self._unit_of_work as unit_of_work:
            return unit_of_work.operations.list_audit_export_requests(workspace_id, limit=limit)

    def get_audit_export_request(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        audit_export_request_id: UUID,
    ) -> AuditExportRequest:
        """按空间读取单条导出状态，跨空间标识统一表现为不存在。"""

        self._require(context, workspace_id, READ_PERMISSION)
        with self._unit_of_work as unit_of_work:
            request = unit_of_work.operations.get_audit_export_request(
                workspace_id, audit_export_request_id
            )
        if request is None:
            raise OperationsNotFoundError
        return request

    def create_audit_export_request(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        actor_id: UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        outcome: AuditOutcome | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        requested_at: datetime | None = None,
    ) -> AuditExportRequest:
        """冻结已授权筛选与字段遮罩，并原子登记导出、审计和 Outbox。"""

        # 1. 校验可信浏览器上下文和请求参数，并在创建时冻结查询上界与字段遮罩。
        self._require(context, workspace_id, READ_PERMISSION)
        if context.authentication_method != "browser_session" or context.user_id is None:
            raise OperationsDeniedError
        if IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None:
            raise OperationsValidationError
        now = requested_at or datetime.now(UTC)
        frozen_occurred_to = occurred_to or now
        _validate_window(1, occurred_from, frozen_occurred_to)
        normalized_action = _optional_filter(action)
        normalized_resource_type = _optional_filter(resource_type)
        request_hash = _audit_export_hash(
            actor_id=actor_id,
            action=normalized_action,
            resource_type=normalized_resource_type,
            outcome=outcome,
            occurred_from=occurred_from,
            occurred_to=frozen_occurred_to,
            field_mask=context.authorized_field_mask,
        )
        # 2. 在同一事务中处理幂等读取，并原子写入导出请求、审计事实和 Outbox 事件。
        try:
            with self._unit_of_work as unit_of_work:
                previous = unit_of_work.operations.get_audit_export_request_by_key(
                    workspace_id, idempotency_key
                )
                if previous is not None:
                    if previous.request_hash != request_hash:
                        raise OperationsIdempotencyConflictError
                    return previous
                export_request = AuditExportRequest(
                    audit_export_request_id=uuid4(),
                    workspace_id=workspace_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    actor_id=actor_id,
                    action=normalized_action,
                    resource_type=normalized_resource_type,
                    outcome=outcome,
                    occurred_from=occurred_from,
                    occurred_to=frozen_occurred_to,
                    field_mask=context.authorized_field_mask,
                    requested_by_actor_id=context.actor_id,
                    requested_by_user_id=context.user_id,
                    request_id=context.request_id,
                    trace_id=context.trace.trace_id,
                    traceparent=context.trace.traceparent,
                    status="pending",
                    attempt_count=0,
                    last_error_code=None,
                    row_count=None,
                    result_sha256=None,
                    result_summary=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                unit_of_work.operations.add_audit_export_request(export_request)
                unit_of_work.audit.add(
                    AuditRecord(
                        audit_id=uuid4(),
                        workspace_id=workspace_id,
                        actor_id=context.actor_id,
                        user_id=context.user_id,
                        action="operations.audit.export.create",
                        resource_type="audit_export_request",
                        resource_id=export_request.audit_export_request_id,
                        outcome="succeeded",
                        occurred_at=now,
                        request_id=context.request_id,
                        trace_id=context.trace.trace_id,
                        traceparent=context.trace.traceparent,
                        authorization=context.audit_authorization,
                        attributes={
                            "has_actor_filter": actor_id is not None,
                            "has_time_window": occurred_from is not None or occurred_to is not None,
                        },
                    )
                )
                unit_of_work.outbox.add(
                    IntegrationEvent(
                        event_id=uuid4(),
                        event_type="operations.audit.export_requested",
                        workspace_id=workspace_id,
                        aggregate_id=export_request.audit_export_request_id,
                        aggregate_version=1,
                        occurred_at=now,
                        trace_id=context.trace.trace_id,
                        traceparent=context.trace.traceparent,
                        actor_id=context.actor_id,
                        user_id=context.user_id,
                        request_id=context.request_id,
                        payload={
                            "audit_export_request_id": str(export_request.audit_export_request_id)
                        },
                    )
                )
                unit_of_work.commit()
                return export_request
        # 3. 并发唯一键冲突后重读首次提交事实，只允许相同摘要复用同一幂等键。
        except IntegrationOperationsWriteConflictError as error:
            with self._unit_of_work as unit_of_work:
                previous = unit_of_work.operations.get_audit_export_request_by_key(
                    workspace_id, idempotency_key
                )
                if previous is None:
                    raise OperationsConflictError from error
                if previous.request_hash != request_hash:
                    raise OperationsIdempotencyConflictError from error
                return previous

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


def _project_audit_record(
    record: AuditOperationsRecord,
    field_mask: frozenset[str],
    *,
    include_attributes: bool,
) -> AuditOperationsRecord:
    """在应用边界执行字段遮罩，列表不携带自由属性。"""

    return AuditOperationsRecord(
        audit_id=record.audit_id,
        workspace_id=record.workspace_id,
        actor_id=None if "actor_id" in field_mask else record.actor_id,
        user_id=None if "user_id" in field_mask else record.user_id,
        action=record.action,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        outcome=record.outcome,
        occurred_at=record.occurred_at,
        request_id=record.request_id,
        trace_id=record.trace_id,
        permission_code=record.permission_code,
        policy_decision_id=record.policy_decision_id,
        policy_version=record.policy_version,
        attributes=(
            _safe_audit_attributes(record.attributes)
            if include_attributes and "attributes" not in field_mask
            else {}
        ),
    )


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


def _safe_audit_attributes(attributes: dict[str, object]) -> dict[str, object]:
    """只保留有界标量；敏感键与嵌套载荷统一显示脱敏占位。"""

    result: dict[str, object] = {}
    for key in sorted(attributes)[:50]:
        value = attributes[key]
        if SENSITIVE_AUDIT_KEY_PATTERN.search(key) or isinstance(value, (dict, list, tuple)):
            result[key] = "[已脱敏]"
        elif value is None or isinstance(value, (str, int, float, bool)):
            result[key] = value[:500] if isinstance(value, str) else value
        else:
            result[key] = str(value)[:500]
    return result


def _audit_export_hash(
    *,
    actor_id: UUID | None,
    action: str | None,
    resource_type: str | None,
    outcome: AuditOutcome | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
    field_mask: frozenset[str],
) -> str:
    """用规范化筛选和可信字段遮罩生成稳定幂等摘要。"""

    payload = {
        "actor_id": str(actor_id) if actor_id is not None else None,
        "action": action,
        "resource_type": resource_type,
        "outcome": outcome,
        "occurred_from": occurred_from.isoformat() if occurred_from else None,
        "occurred_to": occurred_to.isoformat() if occurred_to else None,
        "field_mask": sorted(field_mask),
    }
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
