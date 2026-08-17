"""编排 L3 物理迁移、验证、原子路由切换与失败恢复。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.isolation.application.errors import (
    IsolationConflictError,
    IsolationDeniedError,
    IsolationDependencyError,
    IsolationNotFoundError,
    IsolationValidationError,
)
from ai_platform_api.modules.isolation.application.policy import MIGRATION_TRANSITIONS
from ai_platform_api.modules.isolation.domain.l3 import (
    L3_CHECKPOINT_TYPES,
    L3CheckpointEvidence,
    L3ExecutionEvidence,
    L3IsolationMigrationCheckpoint,
    L3IsolationRecoveryRecord,
    L3IsolationResourceProfile,
    L3IsolationUnitOfWork,
    L3MigrationCommand,
    L3MigrationExecutor,
    L3MigrationVerification,
    L3RecoveryEvidence,
)
from ai_platform_api.modules.isolation.domain.models import (
    InvalidIsolationMigrationTransitionError,
    IsolationWriteConflictError,
    MigrationStatus,
    WorkspaceIsolationMigrationPlan,
    WorkspaceIsolationRoute,
)

L3_NAMESPACE = UUID("58000000-0000-4000-8000-000000000508")
MANAGE_PERMISSION = "workspace.isolation.manage"


class L3IsolationMigrationService:
    """只信任执行器证据，并由服务端决定 L3 路由与唯一写入位置。"""

    def __init__(
        self,
        unit_of_work: L3IsolationUnitOfWork,
        executor: L3MigrationExecutor,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._executor = executor

    def execute_and_verify(
        self,
        context: RequestContext,
        migration_plan_id: UUID,
    ) -> L3MigrationVerification:
        """迁移 L3 资源并冻结六类摘要；目标写入在路由切换前保持关闭。"""

        _require_manage(context)
        plan = self._begin_execution(context, migration_plan_id)
        command = _command(context.workspace_id, migration_plan_id)
        try:
            evidence = self._executor.execute(command)
            profile, checkpoints = _verification_facts(context, command, evidence)
        except IsolationValidationError:
            self._require_rollback(context, migration_plan_id)
            raise
        except Exception as error:
            self._require_rollback(context, migration_plan_id)
            raise IsolationDependencyError from error
        try:
            return self._record_verification(context, plan, profile, checkpoints)
        except IsolationConflictError:
            # 外部复制已完成但控制面未能冻结证据时，必须进入恢复态，不能继续占用 executing。
            self._require_rollback(context, migration_plan_id)
            raise

    def activate_route(
        self,
        context: RequestContext,
        migration_plan_id: UUID,
    ) -> WorkspaceIsolationRoute:
        """把已验证 L3 路由与迁移完成状态放在同一事务中原子提交。"""

        _require_manage(context)
        now = datetime.now(UTC)
        # 1. 锁定空间并重验计划、当前权威路由和六项不可变检查点。
        with self._unit_of_work as unit_of_work:
            unit_of_work.isolation.lock_workspace(context.workspace_id)
            plan = unit_of_work.isolation.get_migration_plan(
                context.workspace_id,
                migration_plan_id,
                for_update=True,
            )
            if plan is None:
                raise IsolationNotFoundError
            if plan.target_level != "L3" or plan.status != "switch_ready":
                raise IsolationConflictError
            current = unit_of_work.isolation.get_current_route(context.workspace_id)
            current_level = current.isolation_level if current is not None else "L1"
            if current_level != plan.from_level:
                raise IsolationConflictError
            profile = unit_of_work.isolation.get_l3_resource_profile(
                context.workspace_id,
                migration_plan_id,
            )
            checkpoints = unit_of_work.isolation.list_l3_checkpoints(
                context.workspace_id,
                migration_plan_id,
            )
            if profile is None or not _checkpoints_passed(checkpoints):
                raise IsolationConflictError
            route = _route(
                context,
                plan,
                profile,
                route_version=unit_of_work.isolation.next_route_version(context.workspace_id),
                occurred_at=now,
            )
            completed = _transition(plan, "completed", context.actor_id, now)
            # 2. 先让数据库按 switch_ready 校验路由，再把完成状态和路由一起提交。
            try:
                # Route Trigger 先按 switch_ready 校验证据，随后状态更新使其成为唯一可读路由。
                unit_of_work.isolation.add_route(route)
                saved = unit_of_work.isolation.save_migration_plan(
                    completed,
                    expected_version=plan.version,
                )
            except IsolationWriteConflictError as error:
                raise IsolationConflictError from error
            if not saved:
                raise IsolationConflictError
            # 3. 路由、状态、审计和事件共享事务，任何写入失败都不暴露半切换状态。
            event, audit = _facts(
                context,
                aggregate_id=route.route_id,
                event_type="workspace.isolation.l3_route_activated",
                action="workspace.isolation.l3_route.activate",
                occurred_at=now,
                attributes={
                    "migration_plan_id": str(migration_plan_id),
                    "route_version": route.route_version,
                    "resource_profile_id": str(profile.resource_profile_id),
                },
            )
            unit_of_work.audit.add(audit)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
            return route

    def recover(
        self,
        context: RequestContext,
        migration_plan_id: UUID,
    ) -> L3IsolationRecoveryRecord:
        """重试清理失败目标；只有源端唯一权威且目标禁写时才结束恢复。"""

        _require_manage(context)
        # 1. 外部清理前先验证计划归属，防止跨空间请求先产生副作用再被数据库拒绝。
        self._assert_recovery_pending(context, migration_plan_id)
        command = _command(context.workspace_id, migration_plan_id)
        try:
            evidence = self._executor.rollback(command)
        except Exception as error:
            raise IsolationDependencyError from error
        _validate_recovery(command, evidence)
        now = datetime.now(UTC)
        # 2. 外部清理后重新锁定计划，冻结本次尝试并处理并发状态漂移。
        with self._unit_of_work as unit_of_work:
            unit_of_work.isolation.lock_workspace(context.workspace_id)
            plan = unit_of_work.isolation.get_migration_plan(
                context.workspace_id,
                migration_plan_id,
                for_update=True,
            )
            if plan is None:
                raise IsolationNotFoundError
            if plan.target_level != "L3" or plan.status != "rollback_required":
                raise IsolationConflictError
            attempt_no = unit_of_work.isolation.next_l3_recovery_attempt(
                context.workspace_id,
                migration_plan_id,
            )
            record = _recovery_record(context, evidence, attempt_no, now)
            try:
                unit_of_work.isolation.add_l3_recovery_record(record)
                if evidence.status == "passed":
                    rolled_back = _transition(plan, "rolled_back", context.actor_id, now)
                    saved = unit_of_work.isolation.save_migration_plan(
                        rolled_back,
                        expected_version=plan.version,
                    )
                    if not saved:
                        raise IsolationConflictError
            except IsolationWriteConflictError as error:
                raise IsolationConflictError from error
            # 3. 失败尝试只追加；只有恢复通过时才和 rolled_back 状态原子提交。
            event, audit = _facts(
                context,
                aggregate_id=record.recovery_record_id,
                event_type="workspace.isolation.l3_recovery_recorded",
                action="workspace.isolation.l3_migration.recover",
                occurred_at=now,
                attributes={
                    "migration_plan_id": str(migration_plan_id),
                    "attempt_no": attempt_no,
                    "status": record.status,
                },
            )
            unit_of_work.audit.add(audit)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
            return record

    def _assert_recovery_pending(
        self,
        context: RequestContext,
        migration_plan_id: UUID,
    ) -> None:
        """在调用外部清理器前确认计划归属和恢复状态，阻止跨空间副作用。"""

        with self._unit_of_work as unit_of_work:
            plan = unit_of_work.isolation.get_migration_plan(
                context.workspace_id,
                migration_plan_id,
            )
            if plan is None:
                raise IsolationNotFoundError
            if plan.target_level != "L3" or plan.status != "rollback_required":
                raise IsolationConflictError

    def _begin_execution(
        self,
        context: RequestContext,
        migration_plan_id: UUID,
    ) -> WorkspaceIsolationMigrationPlan:
        now = datetime.now(UTC)
        # 1. 物理复制前锁定并确认这是已批准但尚未执行的 L3 计划。
        with self._unit_of_work as unit_of_work:
            unit_of_work.isolation.lock_workspace(context.workspace_id)
            plan = unit_of_work.isolation.get_migration_plan(
                context.workspace_id,
                migration_plan_id,
                for_update=True,
            )
            if plan is None:
                raise IsolationNotFoundError
            if plan.target_level != "L3" or plan.status != "approved":
                raise IsolationConflictError
            executing = _transition(plan, "executing", context.actor_id, now)
            try:
                saved = unit_of_work.isolation.save_migration_plan(
                    executing,
                    expected_version=plan.version,
                )
            except IsolationWriteConflictError as error:
                raise IsolationConflictError from error
            if not saved:
                raise IsolationConflictError
            # 2. executing 状态先于外部副作用提交，故障后可确定性进入恢复流程。
            event, audit = _facts(
                context,
                aggregate_id=migration_plan_id,
                event_type="workspace.isolation.l3_migration_started",
                action="workspace.isolation.l3_migration.execute",
                occurred_at=now,
                aggregate_version=executing.version,
                attributes={"source_authority": "source", "target_writes_enabled": False},
            )
            unit_of_work.audit.add(audit)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
            return executing

    def _require_rollback(self, context: RequestContext, migration_plan_id: UUID) -> None:
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            unit_of_work.isolation.lock_workspace(context.workspace_id)
            plan = unit_of_work.isolation.get_migration_plan(
                context.workspace_id,
                migration_plan_id,
                for_update=True,
            )
            if plan is None:
                raise IsolationNotFoundError
            if plan.status == "rollback_required":
                return
            if plan.status != "executing":
                raise IsolationConflictError
            rollback = _transition(plan, "rollback_required", context.actor_id, now)
            try:
                saved = unit_of_work.isolation.save_migration_plan(
                    rollback,
                    expected_version=plan.version,
                )
            except IsolationWriteConflictError as error:
                raise IsolationConflictError from error
            if not saved:
                raise IsolationConflictError
            unit_of_work.commit()

    def _record_verification(
        self,
        context: RequestContext,
        executing: WorkspaceIsolationMigrationPlan,
        profile: L3IsolationResourceProfile,
        checkpoints: tuple[L3IsolationMigrationCheckpoint, ...],
    ) -> L3MigrationVerification:
        now = datetime.now(UTC)
        passed = _checkpoints_passed(checkpoints)
        # 1. 外部复制结束后重新锁定执行代际，拒绝使用过期证据覆盖并发变化。
        with self._unit_of_work as unit_of_work:
            unit_of_work.isolation.lock_workspace(context.workspace_id)
            plan = unit_of_work.isolation.get_migration_plan(
                context.workspace_id,
                executing.migration_plan_id,
                for_update=True,
            )
            if plan is None or plan.status != "executing" or plan.version != executing.version:
                raise IsolationConflictError
            try:
                # 2. 档案和六项检查点先落库，再按结果进入 switch_ready 或 rollback_required。
                unit_of_work.isolation.add_l3_resource_profile(profile)
                unit_of_work.isolation.add_l3_checkpoints(checkpoints)
                if passed:
                    verifying = _transition(plan, "verifying", context.actor_id, now)
                    if not unit_of_work.isolation.save_migration_plan(
                        verifying,
                        expected_version=plan.version,
                    ):
                        raise IsolationConflictError
                    switch_ready = _transition(verifying, "switch_ready", context.actor_id, now)
                    if not unit_of_work.isolation.save_migration_plan(
                        switch_ready,
                        expected_version=verifying.version,
                    ):
                        raise IsolationConflictError
                    final_plan = switch_ready
                else:
                    rollback = _transition(plan, "rollback_required", context.actor_id, now)
                    if not unit_of_work.isolation.save_migration_plan(
                        rollback,
                        expected_version=plan.version,
                    ):
                        raise IsolationConflictError
                    final_plan = rollback
            except IsolationWriteConflictError as error:
                raise IsolationConflictError from error
            # 3. 证据、最终状态、审计和事件在同一事务中形成可追溯结论。
            event, audit = _facts(
                context,
                aggregate_id=profile.resource_profile_id,
                event_type="workspace.isolation.l3_migration_verified",
                action="workspace.isolation.l3_migration.verify",
                occurred_at=now,
                attributes={
                    "migration_plan_id": str(plan.migration_plan_id),
                    "checkpoint_count": len(checkpoints),
                    "status": final_plan.status,
                },
            )
            unit_of_work.audit.add(audit)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
            return L3MigrationVerification(final_plan, profile, checkpoints)


def _verification_facts(
    context: RequestContext,
    command: L3MigrationCommand,
    evidence: L3ExecutionEvidence,
) -> tuple[L3IsolationResourceProfile, tuple[L3IsolationMigrationCheckpoint, ...]]:
    # 1. 先校验执行器身份、单一写入声明和独立密钥指纹，拒绝结构完整但越界的证据。
    if (
        evidence.workspace_id != command.workspace_id
        or evidence.migration_plan_id != command.migration_plan_id
        or evidence.source_is_authoritative is not True
        or evidence.target_writes_enabled is not False
        or not _sha256(evidence.database_identity_digest)
        or not _sha256(evidence.object_storage_identity_digest)
        or not _sha256(evidence.encryption_key_fingerprint)
        or not _sha256(evidence.source_key_fingerprint)
        or evidence.encryption_key_fingerprint == evidence.source_key_fingerprint
    ):
        raise IsolationValidationError
    checkpoint_map = {item.checkpoint_type: item for item in evidence.checkpoints}
    if (
        len(checkpoint_map) != len(evidence.checkpoints)
        or tuple(checkpoint_map) != L3_CHECKPOINT_TYPES
    ):
        raise IsolationValidationError
    for item in evidence.checkpoints:
        _validate_checkpoint(item)
    # 2. 路由键只取服务端 Command；执行器只能返回资源身份摘要，不能覆盖目标路由。
    configuration = {
        "workspace_id": str(context.workspace_id),
        "migration_plan_id": str(command.migration_plan_id),
        "database_route_key": command.database_route_key,
        "object_storage_route_key": command.object_storage_route_key,
        "encryption_key_route_key": command.encryption_key_route_key,
        "search_namespace": command.search_namespace,
        "database_identity_digest": evidence.database_identity_digest,
        "object_storage_identity_digest": evidence.object_storage_identity_digest,
        "encryption_key_fingerprint": evidence.encryption_key_fingerprint,
    }
    configuration_digest = _digest(configuration)
    profile = L3IsolationResourceProfile(
        resource_profile_id=uuid5(L3_NAMESPACE, f"profile:{configuration_digest}"),
        workspace_id=context.workspace_id,
        migration_plan_id=command.migration_plan_id,
        database_route_key=command.database_route_key,
        object_storage_route_key=command.object_storage_route_key,
        encryption_key_route_key=command.encryption_key_route_key,
        search_namespace=command.search_namespace,
        database_identity_digest=evidence.database_identity_digest,
        object_storage_identity_digest=evidence.object_storage_identity_digest,
        encryption_key_fingerprint=evidence.encryption_key_fingerprint,
        configuration_digest=configuration_digest,
        created_by_actor_id=context.actor_id,
        created_at=datetime.now(UTC),
    )
    # 3. 检查点 ID 和位置由配置摘要与冻结顺序确定，重复执行不能改写既有证据。
    checkpoints = tuple(
        _checkpoint(profile, item, position)
        for position, item in enumerate(evidence.checkpoints, start=1)
    )
    return profile, checkpoints


def _checkpoint(
    profile: L3IsolationResourceProfile,
    evidence: L3CheckpointEvidence,
    position: int,
) -> L3IsolationMigrationCheckpoint:
    identity = f"{profile.configuration_digest}:{position}:{evidence.checkpoint_type}"
    return L3IsolationMigrationCheckpoint(
        checkpoint_id=uuid5(L3_NAMESPACE, f"checkpoint:{identity}"),
        resource_profile_id=profile.resource_profile_id,
        workspace_id=profile.workspace_id,
        migration_plan_id=profile.migration_plan_id,
        checkpoint_type=evidence.checkpoint_type,
        position=position,
        status=evidence.status,
        source_digest=evidence.source_digest,
        target_digest=evidence.target_digest,
        evidence_digest=evidence.evidence_digest,
        item_count=evidence.item_count,
        checked_at=profile.created_at,
    )


def _route(
    context: RequestContext,
    plan: WorkspaceIsolationMigrationPlan,
    profile: L3IsolationResourceProfile,
    *,
    route_version: int,
    occurred_at: datetime,
) -> WorkspaceIsolationRoute:
    document = {
        "workspace_id": str(context.workspace_id),
        "route_version": route_version,
        "isolation_level": "L3",
        "database_route_key": profile.database_route_key,
        "object_storage_route_key": profile.object_storage_route_key,
        "encryption_key_route_key": profile.encryption_key_route_key,
        "search_namespace": profile.search_namespace,
        "deployment_route_key": "shared.runtime",
        "migration_plan_id": str(plan.migration_plan_id),
        "resource_profile_digest": profile.configuration_digest,
    }
    digest = _digest(document)
    return WorkspaceIsolationRoute(
        route_id=uuid5(L3_NAMESPACE, f"route:{digest}"),
        workspace_id=context.workspace_id,
        route_version=route_version,
        isolation_level="L3",
        database_route_key=profile.database_route_key,
        object_storage_route_key=profile.object_storage_route_key,
        encryption_key_route_key=profile.encryption_key_route_key,
        search_namespace=profile.search_namespace,
        deployment_route_key="shared.runtime",
        migration_plan_id=plan.migration_plan_id,
        route_digest=digest,
        activated_by_actor_id=context.actor_id,
        activated_at=occurred_at,
    )


def _command(workspace_id: UUID, migration_plan_id: UUID) -> L3MigrationCommand:
    suffix = workspace_id.hex
    return L3MigrationCommand(
        workspace_id=workspace_id,
        migration_plan_id=migration_plan_id,
        database_route_key=f"database.l3.{suffix}",
        object_storage_route_key=f"objects.l3.{suffix}",
        encryption_key_route_key=f"key.l3.{suffix}",
        search_namespace=f"workspace.{suffix}",
        source_is_authoritative=True,
        target_writes_enabled=False,
    )


def _recovery_record(
    context: RequestContext,
    evidence: L3RecoveryEvidence,
    attempt_no: int,
    occurred_at: datetime,
) -> L3IsolationRecoveryRecord:
    identity = _digest(
        {
            "workspace_id": str(context.workspace_id),
            "migration_plan_id": str(evidence.migration_plan_id),
            "attempt_no": attempt_no,
            "status": evidence.status,
            "cleanup_digest": evidence.cleanup_digest,
            "evidence_digest": evidence.evidence_digest,
        }
    )
    return L3IsolationRecoveryRecord(
        recovery_record_id=uuid5(L3_NAMESPACE, f"recovery:{identity}"),
        workspace_id=context.workspace_id,
        migration_plan_id=evidence.migration_plan_id,
        attempt_no=attempt_no,
        status=evidence.status,
        source_is_authoritative=evidence.source_is_authoritative,
        target_writes_enabled=evidence.target_writes_enabled,
        cleanup_digest=evidence.cleanup_digest,
        evidence_digest=evidence.evidence_digest,
        recovered_by_actor_id=context.actor_id,
        recovered_at=occurred_at,
    )


def _validate_checkpoint(evidence: L3CheckpointEvidence) -> None:
    if (
        evidence.status not in {"passed", "failed"}
        or evidence.item_count < 0
        or not _sha256(evidence.source_digest)
        or not _sha256(evidence.target_digest)
        or not _sha256(evidence.evidence_digest)
    ):
        raise IsolationValidationError


def _validate_recovery(command: L3MigrationCommand, evidence: L3RecoveryEvidence) -> None:
    if (
        evidence.workspace_id != command.workspace_id
        or evidence.migration_plan_id != command.migration_plan_id
        or evidence.status not in {"passed", "failed"}
        or not _sha256(evidence.cleanup_digest)
        or not _sha256(evidence.evidence_digest)
        or (evidence.status == "passed" and not evidence.source_is_authoritative)
        or (evidence.status == "passed" and evidence.target_writes_enabled)
    ):
        raise IsolationValidationError


def _checkpoints_passed(
    checkpoints: tuple[L3IsolationMigrationCheckpoint, ...],
) -> bool:
    return tuple(item.checkpoint_type for item in checkpoints) == L3_CHECKPOINT_TYPES and all(
        item.status == "passed" and item.source_digest == item.target_digest for item in checkpoints
    )


def _transition(
    plan: WorkspaceIsolationMigrationPlan,
    target_status: MigrationStatus,
    actor_id: UUID,
    occurred_at: datetime,
) -> WorkspaceIsolationMigrationPlan:
    try:
        return plan.transition(
            target_status,
            actor_id=actor_id,
            occurred_at=occurred_at,
            allowed=MIGRATION_TRANSITIONS,
        )
    except InvalidIsolationMigrationTransitionError as error:
        raise IsolationConflictError from error


def _facts(
    context: RequestContext,
    *,
    aggregate_id: UUID,
    event_type: str,
    action: str,
    occurred_at: datetime,
    attributes: dict[str, object],
    aggregate_version: int = 1,
) -> tuple[IntegrationEvent, AuditRecord]:
    return (
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=context.workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        ),
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="workspace_isolation",
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        ),
    )


def _require_manage(context: RequestContext) -> None:
    if (
        not context.authorized_workspace
        or context.authorized_permission_code != MANAGE_PERMISSION
        or context.audit_authorization is None
    ):
        raise IsolationDeniedError


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _digest(document: object) -> str:
    try:
        payload = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise IsolationValidationError from error
    return hashlib.sha256(payload).hexdigest()
