"""验证 P1C-02 PostgreSQL 角色授权和数据级 ABAC。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.grants import (
    RolePermissionConflictError,
    RolePermissionService,
)
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyRequest,
    ResourceReference,
)
from ai_platform_api.modules.authorization.infrastructure.sqlalchemy import (
    SqlAlchemyPolicyGrantRepository,
    SqlAlchemyRolePermissionUnitOfWork,
)
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.roles_sqlalchemy import (
    SqlAlchemyRoleUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    outbox_events,
    role_permission_grants,
    roles,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select, text

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-8123456789abcdef0123456789abcdef-8123456789abcdef-01")


@dataclass(frozen=True)
class Account:
    account_id: UUID
    personal_workspace_id: UUID
    login_name: str


@dataclass(frozen=True)
class AuthorizationHarness:
    engine: Engine
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    organization: OrganizationService
    roles: RoleService
    permissions: RolePermissionService
    policy: RbacPolicyDecisionPoint


@pytest.fixture(scope="module")
def authorization_database() -> Iterator[AuthorizationHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1c02_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    reader = SqlAlchemyIdentityReader(sessions)
    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    field_registry = load_field_policy_registry(
        ROOT / "contracts/authorization/field-policy-registry.v1.json"
    )
    try:
        yield AuthorizationHarness(
            engine=engine,
            registration=RegistrationService(
                reader,
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            enterprise=EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            organization=OrganizationService(SqlAlchemyOrganizationUnitOfWork(sessions)),
            roles=RoleService(SqlAlchemyRoleUnitOfWork(sessions)),
            permissions=RolePermissionService(
                registry,
                SqlAlchemyRolePermissionUnitOfWork(sessions),
                field_registry,
            ),
            policy=RbacPolicyDecisionPoint(
                registry,
                SqlAlchemyPolicyGrantRepository(sessions),
                field_registry,
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: AuthorizationHarness, name: str) -> Account:
    login_name = f"synthetic.p1c02.{name}@example.com"
    result = harness.registration.register(
        login_name=login_name,
        display_name=f"合成{name}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return Account(result.account_id, result.personal_workspace_id, login_name)


def context(account: Account, workspace_id: UUID | None = None) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id or account.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def join(
    harness: AuthorizationHarness,
    owner: Account,
    member: Account,
) -> tuple[UUID, RequestContext, RequestContext]:
    workspace = harness.enterprise.create(context(owner), name="合成权限企业")
    owner_context = context(owner, workspace.workspace_id)
    invitation = harness.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name=member.login_name,
    )
    harness.enterprise.accept_invitation(
        context(member),
        invitation_id=invitation.invitation_id,
    )
    return workspace.workspace_id, owner_context, context(member, workspace.workspace_id)


def decide(
    harness: AuthorizationHarness,
    request_context: RequestContext,
    permission_code: str,
    resource_type: str,
    resource_id: UUID,
    *,
    attributes: dict[str, object] | None = None,
) -> PolicyDecision:
    return harness.policy.decide(
        PolicyRequest(
            request_context,
            permission_code,
            ResourceReference(
                resource_type,
                resource_id,
                request_context.workspace_id,
                attributes or {},
            ),
        )
    )


def test_system_grants_and_custom_role_scope_are_persistent(
    authorization_database: AuthorizationHarness,
) -> None:
    owner = register(authorization_database, "owner")
    member = register(authorization_database, "member")
    workspace_id, owner_context, member_context = join(authorization_database, owner, member)

    owner_decision = decide(
        authorization_database,
        owner_context,
        "workspace.member.read",
        "workspace_member",
        workspace_id,
    )
    member_write = decide(
        authorization_database,
        member_context,
        "workspace.member.invite",
        "workspace_member",
        workspace_id,
    )
    assert owner_decision.allowed is True
    assert member_write.allowed is False

    root = authorization_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成总部",
        parent_department_id=None,
    )
    child = authorization_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成研发组",
        parent_department_id=root.department_id,
    )
    outside = authorization_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成财务组",
        parent_department_id=None,
    )
    authorization_database.organization.assign_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
        department_ids=(child.department_id,),
        primary_department_id=child.department_id,
        position_ids=(),
    )
    reader_role = authorization_database.roles.create(
        owner_context,
        workspace_id=workspace_id,
        role_key="department_reader",
        name="合成部门读取者",
    )
    authorization_database.roles.bind(
        owner_context,
        workspace_id=workspace_id,
        role_id=reader_role.role_id,
        scope_type="department",
        department_id=root.department_id,
        target_account_id=None,
    )
    grants = authorization_database.permissions.replace(
        owner_context,
        workspace_id=workspace_id,
        role_id=reader_role.role_id,
        entries=(
            (
                "organization.department.read",
                "department_tree",
                frozenset({root.department_id}),
                frozenset(),
                "INTERNAL",
                frozenset(),
            ),
            (
                "workspace.member.read",
                "department_tree",
                frozenset({root.department_id}),
                frozenset(),
                "INTERNAL",
                frozenset({"display_name"}),
            ),
        ),
    )
    assert (
        authorization_database.permissions.list(
            owner_context,
            workspace_id=workspace_id,
            role_id=reader_role.role_id,
        )
        == grants
    )

    child_decision = decide(
        authorization_database,
        member_context,
        "organization.department.read",
        "department",
        child.department_id,
        attributes={"department_id": child.department_id},
    )
    outside_decision = decide(
        authorization_database,
        member_context,
        "organization.department.read",
        "department",
        outside.department_id,
        attributes={"department_id": outside.department_id},
    )
    assert child_decision.allowed is True
    assert outside_decision.allowed is False
    member_decision = decide(
        authorization_database,
        member_context,
        "workspace.member.read",
        "workspace_member",
        workspace_id,
    )
    assert member_decision.allowed is True
    assert member_decision.field_mask == frozenset(
        {"account_id", "display_name", "identity_number", "login_name", "phone_number"}
    )

    with authorization_database.engine.connect() as connection:
        stored = connection.execute(
            select(
                role_permission_grants.c.permission_code,
                role_permission_grants.c.maximum_security_level,
                role_permission_grants.c.field_mask,
            ).where(
                role_permission_grants.c.workspace_id == workspace_id,
                role_permission_grants.c.role_id == reader_role.role_id,
                role_permission_grants.c.permission_code == "organization.department.read",
            )
        ).one()
        stored_field_policy = connection.execute(
            select(
                role_permission_grants.c.maximum_security_level,
                role_permission_grants.c.field_mask,
            ).where(
                role_permission_grants.c.workspace_id == workspace_id,
                role_permission_grants.c.role_id == reader_role.role_id,
                role_permission_grants.c.permission_code == "workspace.member.read",
            )
        ).one()
        audit_count = connection.scalar(
            select(audit_records.c.audit_id).where(
                audit_records.c.action == "authorization.role_permissions.replace"
            )
        )
        event_count = connection.scalar(
            select(outbox_events.c.event_id).where(
                outbox_events.c.event_type == "authorization.role_permissions.replaced"
            )
        )
    assert tuple(stored) == ("organization.department.read", "INTERNAL", [])
    assert tuple(stored_field_policy) == ("INTERNAL", ["display_name"])
    assert audit_count is not None and event_count is not None


def test_system_roles_cannot_be_rewritten(authorization_database: AuthorizationHarness) -> None:
    owner = register(authorization_database, "immutable-owner")
    workspace = authorization_database.enterprise.create(
        context(owner),
        name="合成系统角色保护企业",
    )
    owner_context = context(owner, workspace.workspace_id)
    owner_role = next(
        role
        for role in authorization_database.roles.list_roles(
            owner_context,
            workspace_id=workspace.workspace_id,
        )
        if role.role_key == "workspace_owner"
    )

    with pytest.raises(RolePermissionConflictError):
        authorization_database.permissions.replace(
            owner_context,
            workspace_id=workspace.workspace_id,
            role_id=owner_role.role_id,
            entries=(),
        )


def test_system_role_security_clearance_is_seeded_for_new_workspace(
    authorization_database: AuthorizationHarness,
) -> None:
    owner = register(authorization_database, "security-seed-owner")
    workspace = authorization_database.enterprise.create(
        context(owner),
        name="合成字段密级企业",
    )

    with authorization_database.engine.connect() as connection:
        levels: dict[str, str] = {
            str(row.role_key): str(row.maximum_security_level)
            for row in connection.execute(
                select(roles.c.role_key, role_permission_grants.c.maximum_security_level)
                .join(
                    role_permission_grants,
                    (role_permission_grants.c.workspace_id == roles.c.workspace_id)
                    & (role_permission_grants.c.role_id == roles.c.role_id),
                )
                .where(roles.c.workspace_id == workspace.workspace_id)
                .distinct()
            )
        }

    assert levels == {
        "workspace_member": "INTERNAL",
        "workspace_owner": "RESTRICTED",
    }
