"""编排运营工作台查询与索引维护受控命令。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.operations.contracts import (
    IndexMaintenanceCommand,
    IndexMaintenanceRequest,
    IndexMaintenanceRun,
    LifecycleOperation,
    OperationsIngestionJob,
    OperationsOverview,
)
from ai_platform_api.modules.operations.domain.ports import (
    OperationsWorkbenchUnitOfWork,
    OperationsWorkbenchWriteConflictError,
)

READ_PERMISSION = "operations.records.read"
COMMAND_PERMISSIONS: dict[IndexMaintenanceCommand, str] = {
    "inspection": "operations.index.inspect",
    "full_rebuild": "operations.index.rebuild",
    "cleanup": "operations.index.cleanup",
}
COMMAND_CONFIRMATIONS: dict[IndexMaintenanceCommand, str] = {
    "inspection": "RUN_INDEX_INSPECTION",
    "full_rebuild": "REBUILD_WORKSPACE_INDEX",
    "cleanup": "DELETE_UNRECOVERABLE_INDEX_CHUNKS",
}
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class OperationsWorkbenchDeniedError(PlatformError):
    """当前主体没有目标工作空间的运营读取或维护权限。"""

    error_code = "POLICY_DENIED"


class OperationsWorkbenchValidationError(PlatformError):
    """运营列表或危险操作确认参数不符合稳定契约。"""

    error_code = "VALIDATION_ERROR"


class OperationsWorkbenchIdempotencyConflictError(PlatformError):
    """索引维护幂等键已经绑定到另一份命令。"""

    error_code = "IDEMPOTENCY_CONFLICT"


class OperationsWorkbenchConflictError(PlatformError):
    """索引维护请求发生并发冲突且无法恢复首次事实。"""

    error_code = "OPERATIONS_CONFLICT"


class OperationsWorkbenchService:
    """提供受权限约束的跨模块运营读模型与索引维护命令入口。"""

    def __init__(self, unit_of_work: OperationsWorkbenchUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def overview(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        now: datetime | None = None,
    ) -> OperationsOverview:
        """返回只含低基数状态和计数的工作空间运营快照。"""

        self._require(context, workspace_id, READ_PERMISSION)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.operations.overview(workspace_id, now=now or datetime.now(UTC))

    def list_ingestion_jobs(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[OperationsIngestionJob, ...]:
        """跨知识库返回工作空间入库任务摘要。"""

        self._require_read(context, workspace_id, limit)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.operations.list_ingestion_jobs(workspace_id, limit=limit)

    def list_index_requests(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[IndexMaintenanceRequest, ...]:
        """返回人工索引维护请求及 Worker 恢复状态。"""

        self._require_read(context, workspace_id, limit)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.operations.list_index_requests(workspace_id, limit=limit)

    def list_index_runs(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[IndexMaintenanceRun, ...]:
        """返回当前工作空间已完成的索引维护证据。"""

        self._require_read(context, workspace_id, limit)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.operations.list_index_runs(workspace_id, limit=limit)

    def list_lifecycle_operations(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        limit: int,
    ) -> tuple[LifecycleOperation, ...]:
        """统一返回导出、数据清除和保留期执行历史。"""

        self._require_read(context, workspace_id, limit)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.operations.list_lifecycle_operations(workspace_id, limit=limit)

    def request_index_maintenance(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        command: IndexMaintenanceCommand,
        idempotency_key: str,
        reason_code: str,
        confirmation: str,
        requested_at: datetime | None = None,
    ) -> IndexMaintenanceRequest:
        """原子登记索引维护请求，Worker 后续只消费该公开命令事实。"""

        # 1. 每种命令使用独立静态权限；浏览器主体、原因、确认和幂等键缺一不可。
        permission = COMMAND_PERMISSIONS[command]
        self._require(context, workspace_id, permission)
        if context.authentication_method != "browser_session" or context.user_id is None:
            raise OperationsWorkbenchDeniedError
        if (
            IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None
            or REASON_PATTERN.fullmatch(reason_code) is None
            or confirmation != COMMAND_CONFIRMATIONS[command]
        ):
            raise OperationsWorkbenchValidationError
        request_hash = _request_hash(workspace_id, command, reason_code, confirmation)
        now = requested_at or datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 重复调用只允许复用完全相同的命令事实，不能借旧键改变维护范围或原因。
                previous = unit_of_work.operations.get_index_request(
                    workspace_id,
                    idempotency_key,
                )
                if previous is not None:
                    if previous.request_hash != request_hash:
                        raise OperationsWorkbenchIdempotencyConflictError
                    return previous
                request = IndexMaintenanceRequest(
                    maintenance_request_id=uuid4(),
                    workspace_id=workspace_id,
                    command=command,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    reason_code=reason_code,
                    status="pending",
                    attempt_count=0,
                    last_error_code=None,
                    requested_by_actor_id=context.actor_id,
                    requested_by_user_id=context.user_id,
                    request_id=context.request_id,
                    trace_id=context.trace.trace_id,
                    traceparent=context.trace.traceparent,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                # 3. 请求、审计与 Outbox 同事务提交；事件只传稳定标识，不携带索引或文档正文。
                unit_of_work.operations.add_index_request(request)
                unit_of_work.audit.add(_audit_record(context, request, permission, now))
                unit_of_work.outbox.add(_outbox_event(context, request, now))
                unit_of_work.commit()
                return request
        except OperationsWorkbenchWriteConflictError as error:
            with self._unit_of_work as unit_of_work:
                previous = unit_of_work.operations.get_index_request(
                    workspace_id,
                    idempotency_key,
                )
                if previous is None:
                    raise OperationsWorkbenchConflictError from error
                if previous.request_hash != request_hash:
                    raise OperationsWorkbenchIdempotencyConflictError from error
                return previous

    def _require_read(self, context: RequestContext, workspace_id: UUID, limit: int) -> None:
        self._require(context, workspace_id, READ_PERMISSION)
        if limit < 1 or limit > 200:
            raise OperationsWorkbenchValidationError

    @staticmethod
    def _require(context: RequestContext, workspace_id: UUID, permission_code: str) -> None:
        if (
            context.workspace_id != workspace_id
            or context.authorized_permission_code != permission_code
            or not context.authorized_workspace
        ):
            raise OperationsWorkbenchDeniedError


def _request_hash(
    workspace_id: UUID,
    command: IndexMaintenanceCommand,
    reason_code: str,
    confirmation: str,
) -> str:
    payload = {
        "workspace_id": str(workspace_id),
        "command": command,
        "reason_code": reason_code,
        "confirmation": confirmation,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _audit_record(
    context: RequestContext,
    request: IndexMaintenanceRequest,
    permission: str,
    occurred_at: datetime,
) -> AuditRecord:
    return AuditRecord(
        audit_id=uuid4(),
        workspace_id=request.workspace_id,
        actor_id=context.actor_id,
        user_id=context.user_id,
        action=permission,
        resource_type="index_maintenance_request",
        resource_id=request.maintenance_request_id,
        outcome="succeeded",
        occurred_at=occurred_at,
        request_id=context.request_id,
        trace_id=context.trace.trace_id,
        traceparent=context.trace.traceparent,
        authorization=context.audit_authorization,
        attributes={"command": request.command, "reason_code": request.reason_code},
    )


def _outbox_event(
    context: RequestContext,
    request: IndexMaintenanceRequest,
    occurred_at: datetime,
) -> IntegrationEvent:
    return IntegrationEvent(
        event_id=uuid4(),
        event_type="index.maintenance.requested",
        schema_version=1,
        workspace_id=request.workspace_id,
        aggregate_id=request.maintenance_request_id,
        aggregate_version=1,
        occurred_at=occurred_at,
        trace_id=context.trace.trace_id,
        traceparent=context.trace.traceparent,
        actor_id=context.actor_id,
        user_id=context.user_id,
        request_id=context.request_id,
        payload={
            "maintenance_request_id": str(request.maintenance_request_id),
            "command": request.command,
        },
    )
