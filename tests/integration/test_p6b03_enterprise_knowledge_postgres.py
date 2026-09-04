"""验证 P6B-03 企业分类、团队知识域、隔离和失败关闭。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.enterprise_knowledge.application.service import (
    EnterpriseCategoryHasActiveChildrenError,
    EnterpriseKnowledgeConflictError,
    EnterpriseKnowledgeDeniedError,
    EnterpriseKnowledgeService,
    EnterpriseKnowledgeValidationError,
)
from ai_platform_api.modules.enterprise_knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyEnterpriseKnowledgeUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    audit_records,
    documents,
    outbox_events,
    team_knowledge_domain_rag_policies,
    workspace_memberships,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, func, select, text, update

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
from tests.integration.test_p6b01_enterprise_console_postgres import _create_document
from tests.integration.test_p6b02_team_management_postgres import (
    _create_pre_0077_enterprise,
)
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _migration_database,
)

knowledge_database = _knowledge_database
migration_database = _migration_database

_ENTERPRISE_KNOWLEDGE_PERMISSIONS = (
    "enterprise.knowledge.access",
    "enterprise.knowledge.read",
    "enterprise.category.create",
    "enterprise.category.update",
    "enterprise.category.archive",
    "enterprise.category.bind",
    "enterprise.domain.create",
    "enterprise.domain.update",
    "enterprise.domain.archive",
    "enterprise.domain.scope",
    "enterprise.domain.resolve",
)
_ENTERPRISE_KNOWLEDGE_API_IDS = tuple(
    f"81000000-0000-4000-8000-000000000{suffix}" for suffix in range(189, 199)
)
_ENTERPRISE_KNOWLEDGE_TABLES = {
    "enterprise_categories",
    "enterprise_category_documents",
    "team_knowledge_domains",
    "team_knowledge_domain_rag_policies",
    "team_knowledge_domain_members",
    "team_knowledge_domain_departments",
    "team_knowledge_domain_bases",
}


def _authorized(
    context_value: RequestContext,
    permission_code: str,
    *,
    workspace: bool = True,
    department_ids: frozenset[UUID] = frozenset(),
    account_ids: frozenset[UUID] = frozenset(),
    resource_ids: frozenset[UUID] = frozenset(),
    field_mask: frozenset[str] = frozenset(),
    clearance: SecurityLevel = "RESTRICTED",
) -> RequestContext:
    """构造带可追溯 PDP 证据的合成授权上下文。"""

    return replace(
        context_value,
        authorized_permission_code=permission_code,
        authorized_policy_decision_id=UUID("90000000-0000-4000-8000-000000000931"),
        authorized_policy_version=31,
        authorized_workspace=workspace,
        authorized_department_ids=department_ids,
        authorized_account_ids=account_ids,
        authorized_resource_ids=resource_ids,
        authorized_field_mask=field_mask,
        authorized_maximum_security_level=clearance,
    )


def _membership_id(harness: KnowledgeHarness, workspace_id: UUID, account_id: UUID) -> UUID:
    with harness.engine.connect() as connection:
        value = connection.scalar(
            select(workspace_memberships.c.membership_id).where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == account_id,
            )
        )
    assert isinstance(value, UUID)
    return value


def test_category_lifecycle_masks_restricted_bindings_and_records_transactions(
    knowledge_database: KnowledgeHarness,
) -> None:
    """分类写响应与门户都只暴露当前密级可见文档，并记录原子事实。"""

    owner = register(knowledge_database, identity="p6b03-category-owner")
    member = register(knowledge_database, identity="p6b03-category-member")
    workspace_id, owner_context, _ = join_enterprise(knowledge_database, owner, member)
    service = EnterpriseKnowledgeService(
        SqlAlchemyEnterpriseKnowledgeUnitOfWork(knowledge_database.sessions)
    )
    department = knowledge_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成 P6B-03 治理部",
        parent_department_id=None,
    )
    knowledge_base = knowledge_database.knowledge.create_knowledge_base(
        owner_context, name="合成 P6B-03 分类知识库", default_visibility="workspace"
    )
    public_document_id, _ = _create_document(
        knowledge_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        title="合成公开制度",
        security_level="PUBLIC",
        size_bytes=128,
        published=False,
    )
    restricted_document_id, _ = _create_document(
        knowledge_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        title="合成受限制度",
        security_level="RESTRICTED",
        size_bytes=256,
        published=False,
    )

    created = service.create_category(
        _authorized(owner_context, "enterprise.category.create"),
        workspace_id=workspace_id,
        name="制度治理",
        description="合成分类",
        parent_category_id=None,
        visibility="departments",
        department_ids=(department.department_id,),
        document_ids=(public_document_id, restricted_document_id),
    )
    assert set(created.document_ids) == {public_document_id, restricted_document_id}

    masked = service.get_portal(
        _authorized(
            owner_context,
            "enterprise.knowledge.read",
            clearance="PUBLIC",
            field_mask=frozenset({"title", "display_name"}),
        ),
        workspace_id=workspace_id,
    )
    assert {item.document_id for item in masked.documents} == {public_document_id}
    assert masked.documents[0].title == "受限文档"
    category_projection = next(
        item for item in masked.categories if item.category_id == created.category_id
    )
    assert category_projection.document_ids == (public_document_id,)
    assert all(item.display_name is None for item in masked.members)

    with pytest.raises(EnterpriseKnowledgeValidationError):
        service.replace_category_documents(
            _authorized(owner_context, "enterprise.category.bind", clearance="PUBLIC"),
            workspace_id=workspace_id,
            category_id=created.category_id,
            expected_version=created.version,
            document_ids=(restricted_document_id,),
        )

    updated = service.update_category(
        _authorized(owner_context, "enterprise.category.update"),
        workspace_id=workspace_id,
        category_id=created.category_id,
        expected_version=created.version,
        name="制度治理中心",
        description=None,
        parent_category_id=None,
        visibility="private",
        department_ids=(),
    )
    assert set(updated.document_ids) == {public_document_id, restricted_document_id}
    with pytest.raises(EnterpriseKnowledgeConflictError):
        service.update_category(
            _authorized(owner_context, "enterprise.category.update"),
            workspace_id=workspace_id,
            category_id=created.category_id,
            expected_version=created.version,
            name="过期更新",
            description=None,
            parent_category_id=None,
            visibility="private",
            department_ids=(),
        )

    child = service.create_category(
        _authorized(owner_context, "enterprise.category.create"),
        workspace_id=workspace_id,
        name="子制度",
        description=None,
        parent_category_id=created.category_id,
        visibility="public",
        department_ids=(),
        document_ids=(),
    )
    with pytest.raises(EnterpriseCategoryHasActiveChildrenError):
        service.archive_category(
            _authorized(owner_context, "enterprise.category.archive"),
            workspace_id=workspace_id,
            category_id=created.category_id,
            expected_version=updated.version,
        )
    service.archive_category(
        _authorized(owner_context, "enterprise.category.archive"),
        workspace_id=workspace_id,
        category_id=child.category_id,
        expected_version=child.version,
    )
    archived = service.archive_category(
        _authorized(owner_context, "enterprise.category.archive"),
        workspace_id=workspace_id,
        category_id=created.category_id,
        expected_version=updated.version,
    )
    assert archived.status == "archived"

    with knowledge_database.engine.connect() as connection:
        actions = set(
            connection.scalars(
                select(audit_records.c.action).where(audit_records.c.workspace_id == workspace_id)
            )
        )
        events = set(
            connection.scalars(
                select(outbox_events.c.event_type).where(
                    outbox_events.c.workspace_id == workspace_id
                )
            )
        )
    assert {
        "enterprise.category.create",
        "enterprise.category.update",
        "enterprise.category.archive",
    } <= actions
    assert {
        "enterprise.category.created",
        "enterprise.category.updated",
        "enterprise.category.archived",
    } <= events


def test_domain_scope_intersects_pdp_and_fails_closed_after_status_changes(
    knowledge_database: KnowledgeHarness,
) -> None:
    """成员、部门、知识库和 PDP 任一失效都不得回退到全企业。"""

    owner = register(knowledge_database, identity="p6b03-domain-owner")
    member = register(knowledge_database, identity="p6b03-domain-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    service = EnterpriseKnowledgeService(
        SqlAlchemyEnterpriseKnowledgeUnitOfWork(knowledge_database.sessions)
    )
    member_id = _membership_id(knowledge_database, workspace_id, member.account_id)
    base = knowledge_database.knowledge.create_knowledge_base(
        owner_context, name="合成 P6B-03 团队知识库", default_visibility="workspace"
    )
    document_id, _ = _create_document(
        knowledge_database,
        owner_context,
        base.knowledge_base_id,
        title="合成 P6B-03 团队文档",
        security_level="PUBLIC",
        size_bytes=128,
        published=False,
    )
    domain = service.create_domain(
        _authorized(owner_context, "enterprise.domain.create"),
        workspace_id=workspace_id,
        name="研发知识域",
        description="合成知识域",
        member_ids=(member_id,),
        department_ids=(),
        knowledge_base_ids=(base.knowledge_base_id,),
        rag_mode="balanced",
        top_k=8,
        minimum_score=0.25,
    )

    resolved = service.resolve_domain_scope(
        _authorized(
            member_context,
            "enterprise.domain.resolve",
            workspace=False,
            resource_ids=frozenset({domain.domain_id, document_id}),
        ),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
    )
    assert resolved.effective_knowledge_base_ids == (base.knowledge_base_id,)
    assert resolved.empty_reason == "none"

    pdp_empty = service.resolve_domain_scope(
        _authorized(
            member_context,
            "enterprise.domain.resolve",
            workspace=False,
            resource_ids=frozenset({domain.domain_id}),
        ),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
    )
    assert pdp_empty.effective_knowledge_base_ids == ()
    assert pdp_empty.empty_reason == "pdp_scope_empty"

    unchanged = service.update_domain(
        _authorized(owner_context, "enterprise.domain.update"),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
        expected_version=domain.version,
        name="研发知识域-更新",
        description=None,
        rag_mode="balanced",
        top_k=8,
        minimum_score=0.25,
    )
    assert unchanged.rag_policy.policy_version == 1
    changed = service.update_domain(
        _authorized(owner_context, "enterprise.domain.update"),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
        expected_version=unchanged.version,
        name=unchanged.name,
        description=None,
        rag_mode="precision",
        top_k=5,
        minimum_score=0.5,
    )
    assert changed.rag_policy.policy_version == 2
    with knowledge_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(team_knowledge_domain_rag_policies)
                .where(team_knowledge_domain_rag_policies.c.domain_id == domain.domain_id)
            )
            == 2
        )

    empty = service.replace_domain_scope(
        _authorized(owner_context, "enterprise.domain.scope"),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
        expected_version=changed.version,
        member_ids=(member_id,),
        department_ids=(),
        knowledge_base_ids=(),
    )
    no_bases = service.resolve_domain_scope(
        _authorized(
            member_context,
            "enterprise.domain.resolve",
            resource_ids=frozenset({domain.domain_id, document_id}),
        ),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
    )
    assert no_bases.empty_reason == "no_declared_knowledge_bases"

    service.replace_domain_scope(
        _authorized(owner_context, "enterprise.domain.scope"),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
        expected_version=empty.version,
        member_ids=(member_id,),
        department_ids=(),
        knowledge_base_ids=(base.knowledge_base_id,),
    )
    knowledge_database.enterprise.disable_member(
        owner_context, workspace_id=workspace_id, target_account_id=member.account_id
    )
    inactive = service.resolve_domain_scope(
        _authorized(
            member_context,
            "enterprise.domain.resolve",
            resource_ids=frozenset({domain.domain_id, document_id}),
        ),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
    )
    assert inactive.empty_reason == "member_inactive"
    assert inactive.effective_knowledge_base_ids == ()


def test_domain_scope_maps_document_pdp_union_visibility_and_security(
    knowledge_database: KnowledgeHarness,
) -> None:
    """文档级工作空间、部门、自身、资源和密级授权只映射命中的知识库。"""

    owner = register(knowledge_database, identity="p6b03-scope-owner")
    member = register(knowledge_database, identity="p6b03-scope-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    department = knowledge_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成 P6B-03 范围部门",
        parent_department_id=None,
    )
    knowledge_database.organization.assign_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
        department_ids=(department.department_id,),
        primary_department_id=department.department_id,
        position_ids=(),
    )
    service = EnterpriseKnowledgeService(
        SqlAlchemyEnterpriseKnowledgeUnitOfWork(knowledge_database.sessions)
    )
    bases = {
        "workspace": knowledge_database.knowledge.create_knowledge_base(
            owner_context,
            name="合成 P6B-03 工作空间范围",
            default_visibility="workspace",
        ),
        "private": knowledge_database.knowledge.create_knowledge_base(
            owner_context,
            name="合成 P6B-03 他人私有范围",
            default_visibility="private",
        ),
        "self": knowledge_database.knowledge.create_knowledge_base(
            owner_context,
            name="合成 P6B-03 自身范围",
            default_visibility="private",
        ),
        "department": knowledge_database.knowledge.create_knowledge_base(
            owner_context,
            name="合成 P6B-03 部门范围",
            default_visibility="departments",
            department_ids=frozenset({department.department_id}),
        ),
        "restricted": knowledge_database.knowledge.create_knowledge_base(
            owner_context,
            name="合成 P6B-03 密级范围",
            default_visibility="workspace",
        ),
    }
    document_ids: dict[str, UUID] = {}
    for key, base in bases.items():
        document_id, _ = _create_document(
            knowledge_database,
            owner_context,
            base.knowledge_base_id,
            title=f"合成 P6B-03 {key} 文档",
            security_level="RESTRICTED" if key == "restricted" else "PUBLIC",
            size_bytes=128,
            published=False,
        )
        document_ids[key] = document_id
    # 知识写入口当前仅允许 Owner 创建；测试只替换合成文档创建者以构造 self 授权事实。
    with knowledge_database.engine.begin() as connection:
        connection.execute(
            update(documents)
            .where(documents.c.document_id == document_ids["self"])
            .values(created_by_account_id=member.account_id)
        )

    domain = service.create_domain(
        _authorized(owner_context, "enterprise.domain.create"),
        workspace_id=workspace_id,
        name="合成授权矩阵知识域",
        description=None,
        member_ids=(_membership_id(knowledge_database, workspace_id, member.account_id),),
        department_ids=(),
        knowledge_base_ids=tuple(base.knowledge_base_id for base in bases.values()),
        rag_mode="balanced",
        top_k=8,
        minimum_score=0.25,
    )

    workspace_scope = service.resolve_domain_scope(
        _authorized(
            member_context,
            "enterprise.domain.resolve",
            clearance="PUBLIC",
        ),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
    )
    assert set(workspace_scope.effective_knowledge_base_ids) == {
        bases["workspace"].knowledge_base_id,
        bases["self"].knowledge_base_id,
        bases["department"].knowledge_base_id,
    }
    assert bases["private"].knowledge_base_id not in (workspace_scope.effective_knowledge_base_ids)
    assert bases["restricted"].knowledge_base_id not in (
        workspace_scope.effective_knowledge_base_ids
    )

    cases: tuple[
        tuple[
            str,
            frozenset[UUID],
            frozenset[UUID],
            frozenset[UUID],
            SecurityLevel,
        ],
        ...,
    ] = (
        (
            "department",
            frozenset({department.department_id}),
            frozenset(),
            frozenset({domain.domain_id}),
            "PUBLIC",
        ),
        (
            "self",
            frozenset(),
            frozenset({member.account_id}),
            frozenset({domain.domain_id}),
            "PUBLIC",
        ),
        (
            "private",
            frozenset(),
            frozenset(),
            frozenset({domain.domain_id, document_ids["private"]}),
            "PUBLIC",
        ),
        (
            "restricted",
            frozenset(),
            frozenset(),
            frozenset({domain.domain_id, document_ids["restricted"]}),
            "RESTRICTED",
        ),
    )
    for key, department_ids, account_ids, resource_ids, clearance in cases:
        scope = service.resolve_domain_scope(
            _authorized(
                member_context,
                "enterprise.domain.resolve",
                workspace=False,
                department_ids=department_ids,
                account_ids=account_ids,
                resource_ids=resource_ids,
                clearance=clearance,
            ),
            workspace_id=workspace_id,
            domain_id=domain.domain_id,
        )
        assert scope.effective_knowledge_base_ids == (bases[key].knowledge_base_id,)

    low_clearance = service.resolve_domain_scope(
        _authorized(
            member_context,
            "enterprise.domain.resolve",
            workspace=False,
            resource_ids=frozenset({domain.domain_id, document_ids["restricted"]}),
            clearance="PUBLIC",
        ),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
    )
    assert low_clearance.empty_reason == "pdp_scope_empty"
    assert low_clearance.effective_knowledge_base_ids == ()

    empty_scope = service.resolve_domain_scope(
        _authorized(
            member_context,
            "enterprise.domain.resolve",
            workspace=False,
            resource_ids=frozenset({domain.domain_id}),
        ),
        workspace_id=workspace_id,
        domain_id=domain.domain_id,
    )
    assert empty_scope.empty_reason == "pdp_scope_empty"
    assert empty_scope.effective_knowledge_base_ids == ()


def test_cross_workspace_and_personal_references_are_denied(
    knowledge_database: KnowledgeHarness,
) -> None:
    """个人空间入口与另一企业的成员、文档和知识库引用统一失败关闭。"""

    first_owner = register(knowledge_database, identity="p6b03-first-owner")
    first_member = register(knowledge_database, identity="p6b03-first-member")
    second_owner = register(knowledge_database, identity="p6b03-second-owner")
    second_member = register(knowledge_database, identity="p6b03-second-member")
    first_workspace, first_context, _ = join_enterprise(
        knowledge_database, first_owner, first_member
    )
    second_workspace, second_context, _ = join_enterprise(
        knowledge_database, second_owner, second_member
    )
    service = EnterpriseKnowledgeService(
        SqlAlchemyEnterpriseKnowledgeUnitOfWork(knowledge_database.sessions)
    )
    foreign_base = knowledge_database.knowledge.create_knowledge_base(
        second_context, name="合成外部知识库", default_visibility="workspace"
    )
    foreign_document_id, _ = _create_document(
        knowledge_database,
        second_context,
        foreign_base.knowledge_base_id,
        title="合成外部文档",
        security_level="PUBLIC",
        size_bytes=64,
        published=False,
    )
    foreign_member_id = _membership_id(
        knowledge_database, second_workspace, second_member.account_id
    )

    with pytest.raises(EnterpriseKnowledgeValidationError):
        service.create_category(
            _authorized(first_context, "enterprise.category.create"),
            workspace_id=first_workspace,
            name="非法跨空间分类",
            description=None,
            parent_category_id=None,
            visibility="public",
            department_ids=(),
            document_ids=(foreign_document_id,),
        )
    with pytest.raises(EnterpriseKnowledgeValidationError):
        service.create_domain(
            _authorized(first_context, "enterprise.domain.create"),
            workspace_id=first_workspace,
            name="非法跨空间知识域",
            description=None,
            member_ids=(foreign_member_id,),
            department_ids=(),
            knowledge_base_ids=(foreign_base.knowledge_base_id,),
            rag_mode="balanced",
            top_k=8,
            minimum_score=0.25,
        )
    with pytest.raises(EnterpriseKnowledgeDeniedError):
        service.get_portal(
            _authorized(context(first_owner), "enterprise.knowledge.read"),
            workspace_id=first_owner.personal_workspace_id,
        )


def test_revision_0078_empty_schema_roundtrip(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """空 Schema 往返必须精确维护七张治理表和十个接口绑定。"""

    config, connection, schema, _ = migration_database
    command.upgrade(config, "20260831_0077")
    command.upgrade(config, "20260831_0078")
    connection.commit()
    assert _revision(connection, schema) == "20260831_0078"
    assert _enterprise_knowledge_tables(connection, schema) == _ENTERPRISE_KNOWLEDGE_TABLES
    assert _registered_enterprise_knowledge_binding_count(connection, schema) == 10

    command.downgrade(config, "20260831_0077")
    connection.commit()
    assert _revision(connection, schema) == "20260831_0077"
    assert _enterprise_knowledge_tables(connection, schema) == set()
    assert _registered_enterprise_knowledge_binding_count(connection, schema) == 0

    command.upgrade(config, "20260831_0078")
    connection.commit()
    assert _revision(connection, schema) == "20260831_0078"
    assert _enterprise_knowledge_tables(connection, schema) == _ENTERPRISE_KNOWLEDGE_TABLES


def test_revision_0078_owner_backfill_and_downgrade_guards(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """只回填企业系统 Owner，并保护业务、定制授权和后续菜单事实。"""

    config, connection, schema, database_url = migration_database
    workspace = _seed_pre_0073_workspace(migration_database)
    command.upgrade(config, "20260831_0077")
    connection.commit()
    enterprise_workspace_id = _create_pre_0077_enterprise(
        database_url, schema, workspace.account_id, workspace.workspace_id
    )
    connection.execute(
        text(
            f'DELETE FROM "{schema}".role_permission_grants '
            "WHERE permission_code = ANY(CAST(:permission_codes AS varchar[]))"
        ),
        {"permission_codes": list(_ENTERPRISE_KNOWLEDGE_PERMISSIONS)},
    )
    connection.commit()
    source_release_id, source_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )

    command.upgrade(config, "20260831_0078")
    connection.commit()
    assert _owner_permission_count(connection, schema, enterprise_workspace_id) == 11
    assert _owner_permission_count(connection, schema, workspace.workspace_id) == 0
    upgraded_release_id, upgraded_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )
    assert upgraded_release_id != source_release_id
    assert upgraded_snapshot["registry_version"] == 31
    assert {str(item["menu_id"]) for item in upgraded_snapshot["menus"]} >= {
        "82000000-0000-4000-8000-000000000264"
    }

    command.downgrade(config, "20260831_0077")
    connection.commit()
    restored_release_id, restored_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )
    assert restored_release_id == source_release_id
    assert restored_snapshot == source_snapshot
    assert _owner_permission_count(connection, schema, enterprise_workspace_id) == 0

    command.upgrade(config, "20260831_0078")
    connection.commit()
    _insert_category_guard(connection, schema, enterprise_workspace_id, workspace.account_id)
    with pytest.raises(RuntimeError, match="存在企业分类或团队知识域数据"):
        command.downgrade(config, "20260831_0077")
    connection.rollback()
    connection.execute(
        text(f'DELETE FROM "{schema}".enterprise_categories WHERE name = :name'),
        {"name": "合成 P6B-03 降级保护分类"},
    )
    connection.commit()

    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, 'enterprise.knowledge.read',
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
    with pytest.raises(RuntimeError, match="存在自定义企业知识治理授权"):
        command.downgrade(config, "20260831_0077")
    connection.rollback()
    connection.execute(
        text(
            f'DELETE FROM "{schema}".role_permission_grants '
            "WHERE workspace_id = :workspace_id "
            "AND permission_code = 'enterprise.knowledge.read' "
            f'AND role_id IN (SELECT role_id FROM "{schema}".roles '
            "WHERE role_key = 'workspace_member')"
        ),
        {"workspace_id": enterprise_workspace_id},
    )
    connection.commit()
    _append_followup_menu_release(connection, schema, workspace)
    with pytest.raises(RuntimeError, match="当前菜单发布已在 P6B-03 后变化"):
        command.downgrade(config, "20260831_0077")
    connection.rollback()
    assert _revision(connection, schema) == "20260831_0078"


def _revision(connection: Connection, schema: str) -> str:
    return str(connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')))


def _enterprise_knowledge_tables(connection: Connection, schema: str) -> set[str]:
    return set(
        connection.scalars(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = ANY(:table_names)"
            ),
            {"schema": schema, "table_names": list(_ENTERPRISE_KNOWLEDGE_TABLES)},
        )
    )


def _registered_enterprise_knowledge_binding_count(connection: Connection, schema: str) -> int:
    return int(
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".registered_menu_api_bindings '
                "WHERE api_resource_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": list(_ENTERPRISE_KNOWLEDGE_API_IDS)},
        )
        or 0
    )


def _owner_permission_count(connection: Connection, schema: str, workspace_id: UUID) -> int:
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
            {
                "workspace_id": workspace_id,
                "permission_codes": list(_ENTERPRISE_KNOWLEDGE_PERMISSIONS),
            },
        )
        or 0
    )


def _insert_category_guard(
    connection: Connection, schema: str, workspace_id: UUID, account_id: UUID
) -> None:
    now = datetime.now(UTC)
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".enterprise_categories (
                category_id, workspace_id, parent_category_id, name, description,
                visibility, department_ids, status, created_by_account_id,
                created_at, updated_at, version
            ) VALUES (
                '93000000-0000-4000-8000-000000000931', :workspace_id, NULL,
                '合成 P6B-03 降级保护分类', NULL, 'public', ARRAY[]::uuid[],
                'active', :account_id, :occurred_at, :occurred_at, 1
            )
            """
        ),
        {"workspace_id": workspace_id, "account_id": account_id, "occurred_at": now},
    )
    connection.commit()
