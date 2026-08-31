"""验证 P5-09 法规策略、法律保留、生命周期裁决和数据库防绕过。"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from typing import TypedDict, cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.lifecycle.application.compliance import (
    HOLD_CREATE_PERMISSION,
    HOLD_RELEASE_PERMISSION,
    POLICY_MANAGE_PERMISSION,
    PROOF_READ_PERMISSION,
    LifecycleComplianceConflictError,
    LifecycleComplianceDeniedError,
)
from ai_platform_api.modules.lifecycle.application.service import (
    EXPORT_PERMISSION,
    PURGE_PERMISSION,
    RETENTION_PERMISSION,
    LifecycleComplianceBlockedError,
    LifecycleDeniedError,
    WorkspaceLifecycleService,
)
from ai_platform_api.modules.lifecycle.domain.compliance import LifecycleOperationBlockedError
from ai_platform_api.modules.lifecycle.domain.models import ExportObject, LifecyclePurge
from ai_platform_api.modules.lifecycle.infrastructure.compliance import (
    UnconfiguredRegulatoryPolicySource,
)
from ai_platform_api.modules.lifecycle.infrastructure.sqlalchemy import (
    SqlAlchemyWorkspaceLifecycleStore,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    lifecycle_compliance_proofs,
    lifecycle_legal_hold_releases,
    lifecycle_legal_holds,
    lifecycle_purge_requests,
    lifecycle_regulatory_policy_versions,
    lifecycle_retention_runs,
    menu_releases,
    role_permission_grants,
    workspace_menu_publications,
    workspaces,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _p403_migration_database,
)
from tests.support.p502_quality import RegisteredAccount, authorized_context, register
from tests.support.p503_quality import QualityEvaluationHarness
from tests.support.p509_compliance import (
    StaticRegulatoryPolicySource,
    regulatory_compliance_service,
)

pytest_plugins = ("tests.support.p503_quality_plugin",)
migration_database = _p403_migration_database

ROOT = Path(__file__).parents[2]
REGISTRY_PATH = ROOT / "contracts/lifecycle/workspace-table-registry.v1.json"
COMPLIANCE_PERMISSIONS = frozenset(
    {
        POLICY_MANAGE_PERMISSION,
        HOLD_CREATE_PERMISSION,
        HOLD_RELEASE_PERMISSION,
        PROOF_READ_PERMISSION,
    }
)
COMPLIANCE_MENU_KEYS = frozenset(
    {
        "navigation.workspace.status.regulatory_policy_publish",
        "navigation.workspace.status.legal_hold_activate",
        "navigation.workspace.status.legal_hold_release",
        "navigation.workspace.status.compliance_proof_read",
    }
)
COMPLIANCE_BINDINGS = frozenset(
    {
        (
            "82000000-0000-4000-8000-000000000228",
            "81000000-0000-4000-8000-000000000148",
            "publish",
        ),
        (
            "82000000-0000-4000-8000-000000000229",
            "81000000-0000-4000-8000-000000000149",
            "mutation",
        ),
        (
            "82000000-0000-4000-8000-000000000230",
            "81000000-0000-4000-8000-000000000150",
            "approve",
        ),
        (
            "82000000-0000-4000-8000-000000000231",
            "81000000-0000-4000-8000-000000000151",
            "query",
        ),
    }
)
MIGRATION_NOW = datetime(2026, 8, 17, 0, 0, tzinfo=UTC)


class _MenuSnapshot(TypedDict):
    """描述 P5-09 Migration 断言使用的菜单快照字段。"""

    registry_version: int
    menus: list[dict[str, object]]
    menu_api_bindings: list[dict[str, str]]


@dataclass(frozen=True)
class _MigrationWorkspace:
    """保存非空升级场景中的工作空间、账号和原菜单发布身份。"""

    workspace_id: UUID
    account_id: UUID
    source_release_id: UUID
    source_snapshot: _MenuSnapshot


class SyntheticLifecycleObjectStorage:
    """提供无外部依赖的合成对象边界，用于验证裁决先于存储副作用。"""

    def read_business_objects(self, workspace_id: UUID) -> tuple[ExportObject, ...]:
        del workspace_id
        return ()

    def put_export(self, workspace_id: UUID, export_id: UUID, content: bytes, sha256: str) -> str:
        del content, sha256
        return f"workspaces/{workspace_id}/exports/{export_id}.zip"

    def delete_workspace_objects(self, workspace_id: UUID) -> int:
        del workspace_id
        return 0


class SyntheticLifecycleCache:
    """返回确定性清理计数，不访问真实 Valkey。"""

    def delete_workspace_keys(self, workspace_id: UUID) -> int:
        del workspace_id
        return 0


def _lifecycle(harness: QualityEvaluationHarness) -> WorkspaceLifecycleService:
    return WorkspaceLifecycleService(
        SqlAlchemyWorkspaceLifecycleStore(harness.sessions),
        SyntheticLifecycleObjectStorage(),
        SyntheticLifecycleCache(),
        REGISTRY_PATH,
    )


def _workspace_name(harness: QualityEvaluationHarness, account: RegisteredAccount) -> str:
    with harness.sessions() as session:
        name = session.scalar(
            select(workspaces.c.name).where(workspaces.c.workspace_id == account.workspace_id)
        )
    assert isinstance(name, str)
    return name


def test_p509_unconfigured_jurisdiction_blocks_destructive_operations(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = register(quality_evaluation_database, "p509-unconfigured")
    compliance = regulatory_compliance_service(
        quality_evaluation_database.sessions,
        StaticRegulatoryPolicySource(configured=False),
    )
    policy = compliance.publish_policy(
        authorized_context(owner, POLICY_MANAGE_PERMISSION),
        workspace_id=owner.workspace_id,
    )
    assert policy.jurisdiction_status == "not_configured"
    production_default = UnconfiguredRegulatoryPolicySource().resolve(owner.workspace_id)
    assert production_default.jurisdiction_status == "not_configured"
    assert production_default.external_review_status == "not_configured"

    lifecycle = _lifecycle(quality_evaluation_database)
    with pytest.raises(LifecycleComplianceBlockedError):
        lifecycle.execute_retention(
            authorized_context(owner, RETENTION_PERMISSION),
            workspace_id=owner.workspace_id,
            idempotency_key="synthetic-p509-unconfigured-retention",
        )
    with pytest.raises(LifecycleComplianceBlockedError):
        lifecycle.purge_workspace_business_data(
            authorized_context(owner, PURGE_PERMISSION),
            workspace_id=owner.workspace_id,
            idempotency_key="synthetic-p509-unconfigured-purge",
            confirmed_workspace_name=_workspace_name(quality_evaluation_database, owner),
            reason_code="SYNTHETIC_REQUEST",
        )

    with quality_evaluation_database.sessions() as session:
        decisions = tuple(
            session.scalars(
                select(lifecycle_compliance_proofs.c.decision).where(
                    lifecycle_compliance_proofs.c.workspace_id == owner.workspace_id
                )
            )
        )
        purge_count = session.scalar(
            select(func.count())
            .select_from(lifecycle_purge_requests)
            .where(lifecycle_purge_requests.c.workspace_id == owner.workspace_id)
        )
        retention_count = session.scalar(
            select(func.count())
            .select_from(lifecycle_retention_runs)
            .where(lifecycle_retention_runs.c.workspace_id == owner.workspace_id)
        )
    assert decisions == ("not_configured", "not_configured")
    assert purge_count == 0
    assert retention_count == 0


def test_p509_hold_blocks_destructive_actions_without_expanding_export_access(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = register(quality_evaluation_database, "p509-hold-owner")
    outsider = register(quality_evaluation_database, "p509-hold-outsider")
    compliance = regulatory_compliance_service(quality_evaluation_database.sessions)
    compliance.publish_policy(
        authorized_context(owner, POLICY_MANAGE_PERMISSION),
        workspace_id=owner.workspace_id,
    )
    hold = compliance.activate_legal_hold(
        authorized_context(owner, HOLD_CREATE_PERMISSION),
        workspace_id=owner.workspace_id,
        idempotency_key="synthetic-p509-hold-activate",
        case_reference_digest="a" * 64,
        reason_code="SYNTHETIC_CASE",
    )
    with pytest.raises(LifecycleComplianceDeniedError):
        compliance.activate_legal_hold(
            authorized_context(outsider, HOLD_CREATE_PERMISSION),
            workspace_id=owner.workspace_id,
            idempotency_key="synthetic-p509-cross-workspace",
            case_reference_digest="b" * 64,
            reason_code="SYNTHETIC_CASE",
        )
    with pytest.raises(LifecycleComplianceDeniedError):
        compliance.list_compliance_proofs(
            authorized_context(owner, HOLD_CREATE_PERMISSION),
            workspace_id=owner.workspace_id,
        )

    lifecycle = _lifecycle(quality_evaluation_database)
    with pytest.raises(LifecycleDeniedError):
        lifecycle.export_workspace(
            authorized_context(owner, PROOF_READ_PERMISSION),
            workspace_id=owner.workspace_id,
            idempotency_key="synthetic-p509-export-denied",
        )
    exported = lifecycle.export_workspace(
        authorized_context(owner, EXPORT_PERMISSION),
        workspace_id=owner.workspace_id,
        idempotency_key="synthetic-p509-export-allowed",
    )
    assert exported.status == "completed"

    workspace_name = _workspace_name(quality_evaluation_database, owner)
    with pytest.raises(LifecycleComplianceBlockedError):
        lifecycle.purge_workspace_business_data(
            authorized_context(owner, PURGE_PERMISSION),
            workspace_id=owner.workspace_id,
            idempotency_key="synthetic-p509-blocked-purge",
            confirmed_workspace_name=workspace_name,
            reason_code="SYNTHETIC_REQUEST",
        )
    with pytest.raises(LifecycleComplianceBlockedError):
        lifecycle.execute_retention(
            authorized_context(owner, RETENTION_PERMISSION),
            workspace_id=owner.workspace_id,
            idempotency_key="synthetic-p509-blocked-retention",
        )

    compliance.release_legal_hold(
        authorized_context(owner, HOLD_RELEASE_PERMISSION),
        hold.legal_hold_id,
        workspace_id=owner.workspace_id,
        idempotency_key="synthetic-p509-hold-release",
        release_evidence_digest="c" * 64,
        reason_code="SYNTHETIC_RELEASE",
    )
    # 已提交的阻断证明不能因保留解除而被同一幂等键重新解释。
    with pytest.raises(LifecycleComplianceBlockedError):
        lifecycle.purge_workspace_business_data(
            authorized_context(owner, PURGE_PERMISSION),
            workspace_id=owner.workspace_id,
            idempotency_key="synthetic-p509-blocked-purge",
            confirmed_workspace_name=workspace_name,
            reason_code="SYNTHETIC_REQUEST",
        )
    purge, _ = lifecycle.purge_workspace_business_data(
        authorized_context(owner, PURGE_PERMISSION),
        workspace_id=owner.workspace_id,
        idempotency_key="synthetic-p509-released-purge",
        confirmed_workspace_name=workspace_name,
        reason_code="SYNTHETIC_REQUEST",
    )
    assert purge.status == "completed"

    proofs = compliance.list_compliance_proofs(
        authorized_context(owner, PROOF_READ_PERMISSION),
        workspace_id=owner.workspace_id,
    )
    export_proof = next(item for item in proofs if item.operation_id == exported.export_id)
    assert export_proof.decision == "allowed"
    assert export_proof.active_hold_count == 1
    assert "active_legal_hold" not in export_proof.reason_codes
    assert (
        compliance.list_compliance_proofs(
            authorized_context(outsider, PROOF_READ_PERMISSION),
            workspace_id=outsider.workspace_id,
        )
        == ()
    )


def test_p509_concurrent_hold_and_purge_are_serialized(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = register(quality_evaluation_database, "p509-concurrency")
    compliance = regulatory_compliance_service(quality_evaluation_database.sessions)
    compliance.publish_policy(
        authorized_context(owner, POLICY_MANAGE_PERMISSION),
        workspace_id=owner.workspace_id,
    )
    store = SqlAlchemyWorkspaceLifecycleStore(quality_evaluation_database.sessions)
    now = datetime.now(UTC)
    barrier = Barrier(2)

    def activate_hold() -> str:
        barrier.wait()
        try:
            compliance.activate_legal_hold(
                authorized_context(owner, HOLD_CREATE_PERMISSION),
                workspace_id=owner.workspace_id,
                idempotency_key="synthetic-p509-concurrent-hold",
                case_reference_digest="d" * 64,
                reason_code="SYNTHETIC_CASE",
            )
        except LifecycleComplianceConflictError:
            return "hold_blocked"
        return "hold_active"

    def begin_purge() -> str:
        barrier.wait()
        purge = LifecyclePurge(
            purge_request_id=uuid4(),
            workspace_id=owner.workspace_id,
            idempotency_key="synthetic-p509-concurrent-purge",
            request_hash="e" * 64,
            reason_code="SYNTHETIC_REQUEST",
            confirmed_workspace_name=_workspace_name(quality_evaluation_database, owner),
            status="pending",
            database_cleared=False,
            objects_cleared=False,
            cache_cleared=False,
            deleted_table_counts={},
            deleted_object_count=0,
            deleted_cache_key_count=0,
            last_error_code=None,
            requested_by_account_id=owner.account_id,
            request_id=uuid4(),
            trace_id="5" * 32,
            traceparent="00-" + "5" * 32 + "-" + "2" * 16 + "-01",
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        try:
            store.add_purge(purge)
        except LifecycleOperationBlockedError:
            return "purge_blocked"
        return "purge_pending"

    with ThreadPoolExecutor(max_workers=2) as executor:
        hold_future = executor.submit(activate_hold)
        purge_future = executor.submit(begin_purge)
        results = {hold_future.result(), purge_future.result()}

    assert results in (
        {"hold_active", "purge_blocked"},
        {"hold_blocked", "purge_pending"},
    )
    with quality_evaluation_database.sessions() as session:
        hold_count = session.scalar(
            select(func.count())
            .select_from(lifecycle_legal_holds)
            .where(lifecycle_legal_holds.c.workspace_id == owner.workspace_id)
        )
        purge_count = session.scalar(
            select(func.count())
            .select_from(lifecycle_purge_requests)
            .where(lifecycle_purge_requests.c.workspace_id == owner.workspace_id)
        )
    assert int(hold_count or 0) + int(purge_count or 0) == 1


def test_p509_compliance_facts_are_database_immutable(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = register(quality_evaluation_database, "p509-immutable")
    compliance = regulatory_compliance_service(quality_evaluation_database.sessions)
    policy = compliance.publish_policy(
        authorized_context(owner, POLICY_MANAGE_PERMISSION),
        workspace_id=owner.workspace_id,
    )
    hold = compliance.activate_legal_hold(
        authorized_context(owner, HOLD_CREATE_PERMISSION),
        workspace_id=owner.workspace_id,
        idempotency_key="synthetic-p509-immutable-hold",
        case_reference_digest="f" * 64,
        reason_code="SYNTHETIC_CASE",
    )
    release = compliance.release_legal_hold(
        authorized_context(owner, HOLD_RELEASE_PERMISSION),
        hold.legal_hold_id,
        workspace_id=owner.workspace_id,
        idempotency_key="synthetic-p509-immutable-release",
        release_evidence_digest="1" * 64,
        reason_code="SYNTHETIC_RELEASE",
    )
    exported = _lifecycle(quality_evaluation_database).export_workspace(
        authorized_context(owner, EXPORT_PERMISSION),
        workspace_id=owner.workspace_id,
        idempotency_key="synthetic-p509-immutable-export",
    )
    with quality_evaluation_database.sessions() as session:
        proof_id = session.scalar(
            select(lifecycle_compliance_proofs.c.compliance_proof_id).where(
                lifecycle_compliance_proofs.c.operation_id == exported.export_id
            )
        )
    assert isinstance(proof_id, UUID)

    statements = (
        update(lifecycle_regulatory_policy_versions)
        .where(
            lifecycle_regulatory_policy_versions.c.regulatory_policy_id
            == policy.regulatory_policy_id
        )
        .values(external_review_status="rejected"),
        delete(lifecycle_legal_holds).where(
            lifecycle_legal_holds.c.legal_hold_id == hold.legal_hold_id
        ),
        update(lifecycle_legal_hold_releases)
        .where(lifecycle_legal_hold_releases.c.release_id == release.release_id)
        .values(reason_code="ALTERED_REASON"),
        delete(lifecycle_compliance_proofs).where(
            lifecycle_compliance_proofs.c.compliance_proof_id == proof_id
        ),
    )
    for statement in statements:
        with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
            session.execute(statement)


def test_revision_0068_upgrades_existing_workspace_and_roundtrips(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """非空工作空间升级后获得四项权限、菜单与绑定，并可无损安全降级。"""

    config, connection, schema, _ = migration_database
    workspace = _seed_pre_0068_workspace(migration_database)

    # 1. 升级必须保留已有菜单项，并追加确定性的 Registry 23 发布事实。
    command.upgrade(config, "20260817_0068")
    connection.commit()
    assert _revision(connection, schema) == "20260817_0068"
    assert _owner_compliance_permission_count(connection, schema, workspace.workspace_id) == 4
    assert _registered_compliance_bindings(connection, schema) == COMPLIANCE_BINDINGS
    upgraded_release_id, upgraded_snapshot = _current_menu_snapshot(
        connection,
        schema,
        workspace.workspace_id,
    )
    assert upgraded_release_id != workspace.source_release_id
    assert upgraded_snapshot["registry_version"] == 23
    assert {str(item["menu_key"]) for item in upgraded_snapshot["menus"]} >= (
        COMPLIANCE_MENU_KEYS | {"navigation.workspace.status"}
    )
    snapshot_bindings = {
        (str(item["menu_id"]), str(item["api_resource_id"]), str(item["action_type"]))
        for item in upgraded_snapshot["menu_api_bindings"]
    }
    assert snapshot_bindings >= COMPLIANCE_BINDINGS

    # 2. 没有新增业务事实或管理员配置时，降级精确恢复原发布并回收本节点授权。
    command.downgrade(config, "20260817_0067")
    connection.commit()
    assert _owner_compliance_permission_count(connection, schema, workspace.workspace_id) == 0
    assert _registered_compliance_bindings(connection, schema) == frozenset()
    restored_release_id, restored_snapshot = _current_menu_snapshot(
        connection,
        schema,
        workspace.workspace_id,
    )
    assert restored_release_id == workspace.source_release_id
    assert restored_snapshot == workspace.source_snapshot
    assert (
        connection.scalar(
            text(f'SELECT count(*) FROM "{schema}".menu_releases WHERE release_id = :release_id'),
            {"release_id": upgraded_release_id},
        )
        == 0
    )

    command.upgrade(config, "head")
    connection.commit()
    assert _revision(connection, schema) == "20260831_0076"
    assert _owner_compliance_permission_count(connection, schema, workspace.workspace_id) == 4
    assert _registered_compliance_bindings(connection, schema) == COMPLIANCE_BINDINGS


def test_revision_0068_blocks_downgrade_after_custom_grant_or_later_menu_release(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """管理员授权和后续菜单发布都属于不可静默丢失的降级后事实。"""

    config, connection, schema, _ = migration_database
    workspace = _seed_pre_0068_workspace(migration_database)
    command.upgrade(config, "20260817_0068")
    connection.commit()

    # 1. 非系统 Owner 的法规权限代表管理员配置，必须先显式处理才能降级。
    member_role_id = connection.scalar(
        text(
            f'SELECT role_id FROM "{schema}".roles '
            "WHERE workspace_id = :workspace_id AND role_key = 'workspace_member'"
        ),
        {"workspace_id": workspace.workspace_id},
    )
    assert isinstance(member_role_id, UUID)
    connection.execute(
        text(
            f'INSERT INTO "{schema}".role_permission_grants ('
            "workspace_id, role_id, permission_code, scope_type, department_ids, "
            "resource_ids, maximum_security_level, field_mask) VALUES ("
            ":workspace_id, :role_id, :permission_code, 'workspace', "
            "ARRAY[]::uuid[], ARRAY[]::uuid[], 'RESTRICTED', ARRAY[]::varchar[])"
        ),
        {
            "workspace_id": workspace.workspace_id,
            "role_id": member_role_id,
            "permission_code": POLICY_MANAGE_PERMISSION,
        },
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="自定义法规治理授权"):
        command.downgrade(config, "20260817_0067")
    connection.rollback()
    assert _revision(connection, schema) == "20260817_0068"

    connection.execute(
        text(
            f'DELETE FROM "{schema}".role_permission_grants '
            "WHERE workspace_id = :workspace_id AND role_id = :role_id "
            "AND permission_code = :permission_code"
        ),
        {
            "workspace_id": workspace.workspace_id,
            "role_id": member_role_id,
            "permission_code": POLICY_MANAGE_PERMISSION,
        },
    )
    connection.commit()

    # 2. P5-09 后的菜单发布不能被 Migration 猜测回退目标或直接删除。
    _append_followup_menu_release(connection, schema, workspace)
    with pytest.raises(RuntimeError, match="当前菜单发布已在 P5-09 后变化"):
        command.downgrade(config, "20260817_0067")
    connection.rollback()
    assert _revision(connection, schema) == "20260817_0068"


def _seed_pre_0068_workspace(
    migration_database: tuple[Config, Connection, str, str],
) -> _MigrationWorkspace:
    """在 0067 建立已有 Owner 和非空 Registry 22 菜单发布。"""

    config, connection, schema, database_url = migration_database
    command.upgrade(config, "20260817_0067")
    connection.commit()
    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    try:
        registration = RegistrationService(
            SqlAlchemyIdentityReader(sessions),
            SqlAlchemyRegistrationUnitOfWork(sessions),
            Argon2idPasswordAdapter(),
        ).register(
            login_name=f"synthetic.p509.migration.{uuid4().hex}@example.com",
            display_name="合成 P5-09 迁移用户",
            password="synthetic-password-123",
            request_id=uuid4(),
            trace=TraceContext("7" * 32, "8" * 16),
        )
        source_release_id = uuid4()
        source_snapshot: _MenuSnapshot = {
            "registry_version": 22,
            "menus": [
                {
                    "menu_id": "82000000-0000-4000-8000-000000000005",
                    "menu_key": "navigation.workspace.status",
                }
            ],
            "menu_api_bindings": [
                {
                    "menu_id": "82000000-0000-4000-8000-000000000005",
                    "api_resource_id": "81000000-0000-4000-8000-000000000005",
                    "action_type": "query",
                }
            ],
        }
        digest = _snapshot_digest(source_snapshot)
        with sessions.begin() as session:
            # 当前代码会给新 Owner 写入 P5-09 权限，删除后才能模拟真实 0067 历史状态。
            session.execute(
                delete(role_permission_grants).where(
                    role_permission_grants.c.workspace_id == registration.personal_workspace_id,
                    role_permission_grants.c.permission_code.in_(COMPLIANCE_PERMISSIONS),
                )
            )
            session.execute(
                insert(menu_releases).values(
                    release_id=source_release_id,
                    workspace_id=registration.personal_workspace_id,
                    release_number=1,
                    release_kind="standard",
                    source_release_id=None,
                    status="published",
                    snapshot=source_snapshot,
                    snapshot_digest=digest,
                    validation_errors=[],
                    rejection_reason=None,
                    created_by_account_id=registration.account_id,
                    decided_by_account_id=registration.account_id,
                    created_at=MIGRATION_NOW,
                    validated_at=MIGRATION_NOW,
                    decided_at=MIGRATION_NOW,
                    published_at=MIGRATION_NOW,
                    version=1,
                )
            )
            session.execute(
                insert(workspace_menu_publications).values(
                    workspace_id=registration.personal_workspace_id,
                    current_release_id=source_release_id,
                    published_at=MIGRATION_NOW,
                )
            )
        return _MigrationWorkspace(
            workspace_id=registration.personal_workspace_id,
            account_id=registration.account_id,
            source_release_id=source_release_id,
            source_snapshot=source_snapshot,
        )
    finally:
        engine.dispose()


def _append_followup_menu_release(
    connection: Connection,
    schema: str,
    workspace: _MigrationWorkspace,
) -> None:
    """追加 P5-09 后发布，验证降级不会删除后续管理员事实。"""

    current_release_id, snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )
    followup_release_id = uuid4()
    next_release_number = int(
        connection.scalar(
            text(
                f'SELECT COALESCE(MAX(release_number), 0) + 1 FROM "{schema}".menu_releases '
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace.workspace_id},
        )
        or 1
    )
    connection.execute(
        text(
            f'INSERT INTO "{schema}".menu_releases ('
            "release_id, workspace_id, release_number, release_kind, source_release_id, "
            "status, snapshot, snapshot_digest, validation_errors, rejection_reason, "
            "created_by_account_id, decided_by_account_id, created_at, validated_at, "
            "decided_at, published_at, version) VALUES ("
            ":release_id, :workspace_id, :release_number, 'standard', :source_release_id, "
            "'published', CAST(:snapshot AS jsonb), :snapshot_digest, ARRAY[]::varchar[], "
            "NULL, :account_id, :account_id, :occurred_at, :occurred_at, :occurred_at, "
            ":occurred_at, 7)"
        ),
        {
            "release_id": followup_release_id,
            "workspace_id": workspace.workspace_id,
            "release_number": next_release_number,
            "source_release_id": current_release_id,
            "snapshot": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            "snapshot_digest": _snapshot_digest(snapshot),
            "account_id": workspace.account_id,
            "occurred_at": MIGRATION_NOW,
        },
    )
    connection.execute(
        text(
            f'UPDATE "{schema}".workspace_menu_publications '
            "SET current_release_id = :release_id, published_at = :published_at "
            "WHERE workspace_id = :workspace_id"
        ),
        {
            "release_id": followup_release_id,
            "published_at": MIGRATION_NOW,
            "workspace_id": workspace.workspace_id,
        },
    )
    connection.commit()


def _revision(connection: Connection, schema: str) -> str:
    return cast(
        str,
        connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')),
    )


def _owner_compliance_permission_count(
    connection: Connection,
    schema: str,
    workspace_id: UUID,
) -> int:
    return int(
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".role_permission_grants '
                "WHERE workspace_id = :workspace_id AND permission_code = ANY(:permission_codes)"
            ),
            {
                "workspace_id": workspace_id,
                "permission_codes": list(COMPLIANCE_PERMISSIONS),
            },
        )
        or 0
    )


def _registered_compliance_bindings(
    connection: Connection,
    schema: str,
) -> frozenset[tuple[str, str, str]]:
    rows = connection.execute(
        text(
            f'SELECT menu_id, api_resource_id, action_type FROM "{schema}".'
            "registered_menu_api_bindings WHERE menu_id = ANY(:menu_ids)"
        ),
        {"menu_ids": [UUID(menu_id) for menu_id, _, _ in COMPLIANCE_BINDINGS]},
    )
    return frozenset(
        (str(row.menu_id), str(row.api_resource_id), str(row.action_type)) for row in rows
    )


def _current_menu_snapshot(
    connection: Connection,
    schema: str,
    workspace_id: UUID,
) -> tuple[UUID, _MenuSnapshot]:
    row = connection.execute(
        text(
            f"SELECT releases.release_id, releases.snapshot "
            f'FROM "{schema}".workspace_menu_publications AS publications '
            f'JOIN "{schema}".menu_releases AS releases '
            "ON releases.workspace_id = publications.workspace_id "
            "AND releases.release_id = publications.current_release_id "
            "WHERE publications.workspace_id = :workspace_id"
        ),
        {"workspace_id": workspace_id},
    ).one()
    return cast(UUID, row.release_id), cast(_MenuSnapshot, row.snapshot)


def _snapshot_digest(snapshot: _MenuSnapshot) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
