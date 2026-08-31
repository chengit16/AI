"""验证 P6B-02 团队治理 PostgreSQL 事务、隔离与 Migration 往返。"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.enterprise import (
    EnterpriseWorkspaceService,
    WorkspaceGovernanceDeniedError,
    WorkspaceLifecycleConflictError,
)
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.application.team_management import TeamManagementService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.roles_sqlalchemy import (
    SqlAlchemyRoleUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.team_management_sqlalchemy import (
    SqlAlchemyTeamManagementUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    membership_departments,
    membership_positions,
    outbox_events,
    role_bindings,
    workspace_memberships,
    workspaces,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, func, select, text

from tests.integration.test_p1d01_knowledge_postgres import (
    KnowledgeHarness,
    context,
    join_enterprise,
    register,
)
from tests.integration.test_p1d01_knowledge_postgres import (
    knowledge_database as _knowledge_database,
)
from tests.integration.test_p6a03_document_download_postgres import (
    _append_followup_menu_release,
    _current_menu_snapshot,
    _seed_pre_0073_workspace,
)
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _migration_database,
)

knowledge_database = _knowledge_database
migration_database = _migration_database

_TEAM_PERMISSIONS = (
    "workspace.team.read",
    "workspace.invitation.cancel",
    "workspace.member.update",
    "workspace.member.activate",
    "workspace.member.remove",
)
_TEAM_API_IDS = tuple(f"81000000-0000-4000-8000-000000000{suffix}" for suffix in range(184, 189))
_TEAM_MENU_IDS = tuple(f"82000000-0000-4000-8000-000000000{suffix}" for suffix in range(259, 264))


def _authorized(
    context_value: RequestContext,
    permission_code: str,
    *,
    field_mask: frozenset[str] = frozenset(),
) -> RequestContext:
    """冻结真实团队用例所需的全空间 PDP 决策。"""

    return replace(
        context_value,
        authorized_permission_code=permission_code,
        authorized_policy_decision_id=UUID("90000000-0000-4000-8000-000000000930"),
        authorized_policy_version=30,
        authorized_workspace=True,
        authorized_field_mask=field_mask,
        authorized_maximum_security_level="RESTRICTED",
    )


def test_team_management_real_transaction_preserves_then_removes_assignments(
    knowledge_database: KnowledgeHarness,
) -> None:
    """成员配置、停用、恢复和移除必须按同一角色版本与事务边界收敛。"""

    owner = register(knowledge_database, identity="p6b02-owner")
    member = register(knowledge_database, identity="p6b02-member")
    invited = register(knowledge_database, identity="p6b02-invited")
    outsider = register(knowledge_database, identity="p6b02-outsider")
    workspace_id, owner_context, _ = join_enterprise(knowledge_database, owner, member)
    invitation = knowledge_database.enterprise.invite(
        owner_context,
        workspace_id=workspace_id,
        login_name=invited.login_name,
    )
    organization = OrganizationService(
        SqlAlchemyOrganizationUnitOfWork(knowledge_database.sessions)
    )
    role_service = RoleService(SqlAlchemyRoleUnitOfWork(knowledge_database.sessions))
    team = TeamManagementService(SqlAlchemyTeamManagementUnitOfWork(knowledge_database.sessions))

    department = organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成 P6B-02 研发部",
        parent_department_id=None,
    )
    position = organization.create_position(
        owner_context,
        workspace_id=workspace_id,
        department_id=department.department_id,
        name="合成 P6B-02 工程师",
    )
    custom_role = role_service.create(
        owner_context,
        workspace_id=workspace_id,
        role_key="p6b02_reviewer",
        name="合成 P6B-02 审核员",
    )

    initial = team.get_snapshot(
        _authorized(owner_context, "workspace.team.read"),
        workspace_id=workspace_id,
    )
    member_before = next(item for item in initial.members if item.account_id == member.account_id)
    assert next(
        item for item in initial.invitations if item.invitation_id == invitation.invitation_id
    ).status == ("pending")
    assert member_before.last_active_at is not None
    with knowledge_database.engine.connect() as connection:
        role_version_before = connection.scalar(
            select(workspaces.c.role_version).where(workspaces.c.workspace_id == workspace_id)
        )
        assert role_version_before is not None

    configured = team.update_member(
        _authorized(owner_context, "workspace.member.update"),
        workspace_id=workspace_id,
        target_account_id=member.account_id,
        expected_version=member_before.version,
        department_ids=(department.department_id,),
        primary_department_id=department.department_id,
        position_ids=(position.position_id,),
        direct_role_ids=(custom_role.role_id,),
    )
    assert configured.version == member_before.version + 1
    configured_snapshot = team.get_snapshot(
        _authorized(owner_context, "workspace.team.read"),
        workspace_id=workspace_id,
    )
    member_configured = next(
        item for item in configured_snapshot.members if item.account_id == member.account_id
    )
    assert member_configured.department_ids == (department.department_id,)
    assert member_configured.position_ids == (position.position_id,)
    assert member_configured.direct_role_ids == (custom_role.role_id,)
    assert custom_role.role_id in {item.role_id for item in member_configured.effective_roles}

    knowledge_database.enterprise.disable_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    disabled_snapshot = team.get_snapshot(
        _authorized(owner_context, "workspace.team.read"),
        workspace_id=workspace_id,
    )
    disabled = next(
        item for item in disabled_snapshot.members if item.account_id == member.account_id
    )
    assert disabled.status == "disabled"
    assert disabled.department_ids == (department.department_id,)
    assert disabled.position_ids == (position.position_id,)
    assert disabled.direct_role_ids == (custom_role.role_id,)
    assert disabled.effective_roles == ()

    restored = team.activate_member(
        _authorized(owner_context, "workspace.member.activate"),
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    assert restored.status == "active"
    restored_snapshot = team.get_snapshot(
        _authorized(owner_context, "workspace.team.read"),
        workspace_id=workspace_id,
    )
    restored_member = next(
        item for item in restored_snapshot.members if item.account_id == member.account_id
    )
    assert custom_role.role_id in {item.role_id for item in restored_member.effective_roles}

    with pytest.raises(WorkspaceLifecycleConflictError):
        team.update_member(
            _authorized(owner_context, "workspace.member.update"),
            workspace_id=workspace_id,
            target_account_id=member.account_id,
            expected_version=member_before.version,
            department_ids=(department.department_id,),
            primary_department_id=department.department_id,
            position_ids=(),
            direct_role_ids=(),
        )

    cancelled = team.cancel_invitation(
        _authorized(owner_context, "workspace.invitation.cancel"),
        workspace_id=workspace_id,
        invitation_id=invitation.invitation_id,
    )
    assert cancelled.status == "cancelled"
    removed = team.remove_member(
        _authorized(owner_context, "workspace.member.remove"),
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    assert removed.status == "left"

    with knowledge_database.engine.connect() as connection:
        membership_id = connection.scalar(
            select(workspace_memberships.c.membership_id).where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == member.account_id,
            )
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(membership_departments)
                .where(membership_departments.c.membership_id == membership_id)
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(membership_positions)
                .where(membership_positions.c.membership_id == membership_id)
            )
            == 0
        )
        binding = connection.execute(
            select(role_bindings.c.status, role_bindings.c.revoked_at).where(
                role_bindings.c.workspace_id == workspace_id,
                role_bindings.c.membership_id == membership_id,
                role_bindings.c.role_id == custom_role.role_id,
            )
        ).one()
        assert binding.status == "revoked" and binding.revoked_at is not None
        assert (
            connection.scalar(
                select(workspaces.c.role_version).where(workspaces.c.workspace_id == workspace_id)
            )
            == role_version_before + 4
        )
        actions = set(
            connection.scalars(
                select(audit_records.c.action).where(audit_records.c.workspace_id == workspace_id)
            )
        )
        event_types = set(
            connection.scalars(
                select(outbox_events.c.event_type).where(
                    outbox_events.c.workspace_id == workspace_id
                )
            )
        )
    assert {
        "workspace.member.configuration.update",
        "workspace.member.activate",
        "workspace.member.remove",
        "workspace.invitation.cancel",
    } <= actions
    assert {
        "workspace.member.configuration.updated",
        "workspace.member.activated",
        "workspace.member.removed",
        "workspace.invitation.cancelled",
    } <= event_types

    masked = team.get_snapshot(
        _authorized(
            owner_context,
            "workspace.team.read",
            field_mask=frozenset({"display_name", "login_name"}),
        ),
        workspace_id=workspace_id,
    )
    assert all(item.display_name is None and item.login_name is None for item in masked.members)
    with pytest.raises(WorkspaceGovernanceDeniedError):
        team.get_snapshot(
            _authorized(
                owner_context,
                "workspace.team.read",
                field_mask=frozenset({"account_id"}),
            ),
            workspace_id=workspace_id,
        )

    outsider_workspace = knowledge_database.enterprise.create(
        context(outsider), name="合成 P6B-02 隔离企业"
    )
    with pytest.raises(WorkspaceGovernanceDeniedError):
        team.get_snapshot(
            _authorized(owner_context, "workspace.team.read"),
            workspace_id=outsider_workspace.workspace_id,
        )
    with pytest.raises(WorkspaceGovernanceDeniedError):
        team.get_snapshot(
            _authorized(context(owner), "workspace.team.read"),
            workspace_id=owner.personal_workspace_id,
        )


def test_revision_0077_empty_schema_roundtrip(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """空 Schema 升级、降级和再升级必须精确维护五组团队绑定。"""

    config, connection, schema, _ = migration_database
    command.upgrade(config, "20260831_0076")
    command.upgrade(config, "20260831_0077")
    connection.commit()
    assert _revision(connection, schema) == "20260831_0077"
    assert _registered_team_binding_count(connection, schema) == 5

    command.downgrade(config, "20260831_0076")
    connection.commit()
    assert _revision(connection, schema) == "20260831_0076"
    assert _registered_team_binding_count(connection, schema) == 0
    command.upgrade(config, "20260831_0077")
    connection.commit()
    assert _revision(connection, schema) == "20260831_0077"


def test_revision_0077_existing_owner_snapshot_and_downgrade_guards(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """存量企业 Owner 与快照可精确升级，定制授权和后续发布受保护。"""

    config, connection, schema, database_url = migration_database
    workspace = _seed_pre_0073_workspace(migration_database)
    command.upgrade(config, "20260831_0076")
    connection.commit()
    enterprise_workspace_id = _create_pre_0077_enterprise(
        database_url, schema, workspace.account_id, workspace.workspace_id
    )
    connection.execute(
        text(
            f'DELETE FROM "{schema}".role_permission_grants '
            "WHERE permission_code = ANY(CAST(:permission_codes AS varchar[]))"
        ),
        {"permission_codes": list(_TEAM_PERMISSIONS)},
    )
    connection.commit()
    source_release_id, source_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )

    command.upgrade(config, "20260831_0077")
    connection.commit()
    assert _owner_team_permission_count(connection, schema, enterprise_workspace_id) == 5
    assert _owner_team_permission_count(connection, schema, workspace.workspace_id) == 0
    upgraded_release_id, upgraded_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )
    assert upgraded_release_id != source_release_id
    assert upgraded_snapshot["registry_version"] == 30
    assert {str(item["menu_id"]) for item in upgraded_snapshot["menus"]} >= set(_TEAM_MENU_IDS)

    command.downgrade(config, "20260831_0076")
    connection.commit()
    restored_release_id, restored_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )
    assert restored_release_id == source_release_id
    assert restored_snapshot == source_snapshot
    assert _owner_team_permission_count(connection, schema, enterprise_workspace_id) == 0

    command.upgrade(config, "20260831_0077")
    connection.commit()
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, 'workspace.team.read',
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            WHERE roles.workspace_id = :workspace_id AND roles.role_key = 'workspace_member'
            ON CONFLICT DO NOTHING
            """
        ),
        {"workspace_id": enterprise_workspace_id},
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="存在自定义团队治理授权"):
        command.downgrade(config, "20260831_0076")
    connection.rollback()
    connection.execute(
        text(
            f'DELETE FROM "{schema}".role_permission_grants '
            "WHERE workspace_id = :workspace_id AND permission_code = 'workspace.team.read' "
            f'AND role_id IN (SELECT role_id FROM "{schema}".roles '
            "WHERE role_key = 'workspace_member')"
        ),
        {"workspace_id": enterprise_workspace_id},
    )
    connection.commit()
    _append_followup_menu_release(connection, schema, workspace)
    with pytest.raises(RuntimeError, match="当前菜单发布已在 P6B-02 后变化"):
        command.downgrade(config, "20260831_0076")
    connection.rollback()
    assert _revision(connection, schema) == "20260831_0077"


def _create_pre_0077_enterprise(
    database_url: str,
    schema: str,
    account_id: UUID,
    personal_workspace_id: UUID,
) -> UUID:
    """使用既有账号创建企业，再由测试移除当前代码提前注入的团队权限。"""

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    try:
        service = EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions))
        workspace = service.create(
            RequestContext.trusted(
                actor_id=account_id,
                user_id=account_id,
                workspace_id=personal_workspace_id,
                authentication_method="browser_session",
                trace=workspace_trace(),
            ),
            name="合成 P6B-02 迁移企业",
        )
        return workspace.workspace_id
    finally:
        engine.dispose()


def workspace_trace() -> TraceContext:
    """复用稳定低敏 Trace 构造迁移合成企业。"""

    return TraceContext("e" * 32, "f" * 16)


def _revision(connection: Connection, schema: str) -> str:
    return str(connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')))


def _registered_team_binding_count(connection: Connection, schema: str) -> int:
    return int(
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".registered_menu_api_bindings '
                "WHERE api_resource_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": list(_TEAM_API_IDS)},
        )
        or 0
    )


def _owner_team_permission_count(connection: Connection, schema: str, workspace_id: UUID) -> int:
    return int(
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".role_permission_grants AS grants '
                f'JOIN "{schema}".roles AS roles '
                "ON roles.workspace_id = grants.workspace_id AND roles.role_id = grants.role_id "
                "WHERE grants.workspace_id = :workspace_id "
                "AND roles.role_key = 'workspace_owner' "
                "AND grants.permission_code = ANY(:permission_codes)"
            ),
            {"workspace_id": workspace_id, "permission_codes": list(_TEAM_PERMISSIONS)},
        )
        or 0
    )
