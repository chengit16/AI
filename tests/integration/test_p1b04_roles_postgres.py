"""验证 P1B-04 角色、绑定、继承和缓存失效。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import (
    RoleConflictError,
    RoleGovernanceDeniedError,
    RoleNotFoundError,
    RoleService,
)
from ai_platform_api.modules.identity.domain.roles import EffectiveRoleSet
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.role_cache import ValkeyRoleResolutionCache
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
    role_bindings,
    workspace_memberships,
    workspaces,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, insert, select, text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/14"
TRACE = TraceContext.continue_from("00-3123456789abcdef0123456789abcdef-3123456789abcdef-01")


@dataclass(frozen=True)
class RegisteredAccount:
    account_id: UUID
    personal_workspace_id: UUID
    login_name: str


class CountingRoleCache:
    """保留真实 Valkey 读写，并暴露命中次数验证应用层确实复用了缓存。"""

    def __init__(self, cache: ValkeyRoleResolutionCache) -> None:
        self._cache = cache
        self.hits = 0
        self.misses = 0

    def get(
        self, workspace_id: UUID, membership_id: UUID, role_version: int
    ) -> EffectiveRoleSet | None:
        result = self._cache.get(workspace_id, membership_id, role_version)
        if result is None:
            self.misses += 1
        else:
            self.hits += 1
        return result

    def put(self, role_set: EffectiveRoleSet) -> None:
        self._cache.put(role_set)


@dataclass(frozen=True)
class RoleHarness:
    engine: Engine
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    organization: OrganizationService
    roles: RoleService
    role_cache: CountingRoleCache


@pytest.fixture(scope="module")
def role_database() -> Iterator[RoleHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    schema = f"p1b04_test_{uuid4().hex}"
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
    cache = ValkeyRoleResolutionCache(valkey_url)
    counting_cache = CountingRoleCache(cache)
    try:
        yield RoleHarness(
            engine=engine,
            registration=RegistrationService(
                repository=reader,
                unit_of_work=SqlAlchemyRegistrationUnitOfWork(sessions),
                passwords=Argon2idPasswordAdapter(),
            ),
            enterprise=EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            organization=OrganizationService(SqlAlchemyOrganizationUnitOfWork(sessions)),
            roles=RoleService(SqlAlchemyRoleUnitOfWork(sessions), counting_cache),
            role_cache=counting_cache,
        )
    finally:
        cache.close()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(
    harness: RoleHarness,
    *,
    login_name: str,
    display_name: str,
) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=login_name,
        display_name=display_name,
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id, login_name)


def context(account: RegisteredAccount, workspace_id: UUID | None = None) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id or account.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def join_workspace(
    harness: RoleHarness,
    owner: RegisteredAccount,
    member: RegisteredAccount,
) -> tuple[UUID, RequestContext, RequestContext]:
    workspace = harness.enterprise.create(context(owner), name="合成角色测试企业")
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


def test_system_roles_and_read_boundaries_are_deterministic(
    role_database: RoleHarness,
) -> None:
    owner = register(
        role_database,
        login_name="synthetic.role.owner.p1b04@example.com",
        display_name="合成角色所有者",
    )
    member = register(
        role_database,
        login_name="synthetic.role.member.p1b04@example.com",
        display_name="合成角色成员",
    )
    workspace_id, owner_context, member_context = join_workspace(role_database, owner, member)

    listed = role_database.roles.list_roles(owner_context, workspace_id=workspace_id)
    assert [(item.role_key, item.system_managed) for item in listed] == [
        ("workspace_member", True),
        ("workspace_owner", True),
    ]
    owner_roles = role_database.roles.effective_roles(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=owner.account_id,
    )
    member_roles = role_database.roles.effective_roles(
        member_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    assert [item.role_key for item in owner_roles.roles] == [
        "workspace_member",
        "workspace_owner",
    ]
    assert [item.role_key for item in member_roles.roles] == ["workspace_member"]

    with pytest.raises(RoleGovernanceDeniedError):
        role_database.roles.list_roles(member_context, workspace_id=workspace_id)
    with pytest.raises(RoleGovernanceDeniedError):
        role_database.roles.effective_roles(
            member_context,
            workspace_id=workspace_id,
            target_account_id=owner.account_id,
        )
    with pytest.raises(RoleGovernanceDeniedError):
        role_database.roles.create(
            context(owner),
            workspace_id=owner.personal_workspace_id,
            role_key="personal_admin",
            name="个人空间管理员",
        )
    with pytest.raises(RoleConflictError):
        role_database.roles.set_status(
            owner_context,
            workspace_id=workspace_id,
            role_id=listed[0].role_id,
            active=False,
        )


def test_role_inheritance_cache_and_version_invalidation(role_database: RoleHarness) -> None:
    owner = register(
        role_database,
        login_name="synthetic.inherit.owner.p1b04@example.com",
        display_name="合成继承所有者",
    )
    member = register(
        role_database,
        login_name="synthetic.inherit.member.p1b04@example.com",
        display_name="合成继承成员",
    )
    workspace_id, owner_context, _ = join_workspace(role_database, owner, member)
    root = role_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成总部",
        parent_department_id=None,
    )
    child = role_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成平台组",
        parent_department_id=root.department_id,
    )
    role_database.organization.assign_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
        department_ids=(child.department_id,),
        primary_department_id=child.department_id,
        position_ids=(),
    )
    lead = role_database.roles.create(
        owner_context,
        workspace_id=workspace_id,
        role_key="department_lead",
        name="合成部门负责人",
    )
    department_binding = role_database.roles.bind(
        owner_context,
        workspace_id=workspace_id,
        role_id=lead.role_id,
        scope_type="department",
        department_id=root.department_id,
        target_account_id=None,
    )
    member_binding = role_database.roles.bind(
        owner_context,
        workspace_id=workspace_id,
        role_id=lead.role_id,
        scope_type="member",
        department_id=None,
        target_account_id=member.account_id,
    )

    initial_misses = role_database.role_cache.misses
    initial_hits = role_database.role_cache.hits
    first = role_database.roles.effective_roles(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    second = role_database.roles.effective_roles(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    assert second == first
    assert role_database.role_cache.misses == initial_misses + 1
    assert role_database.role_cache.hits == initial_hits + 1
    inherited = next(item for item in first.roles if item.role_id == lead.role_id)
    assert [(item.scope_type, item.scope_id) for item in inherited.sources] == [
        ("department", root.department_id),
        ("member", first.membership_id),
    ]

    role_database.roles.revoke(
        owner_context,
        workspace_id=workspace_id,
        binding_id=member_binding.binding_id,
    )
    after_revoke = role_database.roles.effective_roles(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    assert after_revoke.role_version == first.role_version + 1
    assert role_database.role_cache.misses == initial_misses + 2
    assert next(item for item in after_revoke.roles if item.role_id == lead.role_id).sources == (
        inherited.sources[0],
    )

    role_database.organization.set_department_status(
        owner_context,
        workspace_id=workspace_id,
        department_id=root.department_id,
        active=False,
    )
    after_disable = role_database.roles.effective_roles(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    assert after_disable.role_version == after_revoke.role_version + 1
    assert lead.role_id not in {item.role_id for item in after_disable.roles}

    with pytest.raises(RoleConflictError):
        role_database.roles.revoke(
            owner_context,
            workspace_id=workspace_id,
            binding_id=member_binding.binding_id,
        )
    assert department_binding.status == "active"


def test_member_lifecycle_and_database_reject_cross_workspace_binding(
    role_database: RoleHarness,
) -> None:
    first_owner = register(
        role_database,
        login_name="synthetic.cross.owner.p1b04@example.com",
        display_name="合成跨空间所有者",
    )
    member = register(
        role_database,
        login_name="synthetic.lifecycle.member.p1b04@example.com",
        display_name="合成生命周期成员",
    )
    workspace_id, owner_context, _ = join_workspace(role_database, first_owner, member)
    direct_role = role_database.roles.create(
        owner_context,
        workspace_id=workspace_id,
        role_key="direct_operator",
        name="合成直接操作员",
    )
    direct_binding = role_database.roles.bind(
        owner_context,
        workspace_id=workspace_id,
        role_id=direct_role.role_id,
        scope_type="member",
        department_id=None,
        target_account_id=member.account_id,
    )
    before_disable = role_database.roles.effective_roles(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    role_database.enterprise.disable_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    with pytest.raises(RoleNotFoundError):
        role_database.roles.effective_roles(
            owner_context,
            workspace_id=workspace_id,
            target_account_id=member.account_id,
        )
    with role_database.engine.connect() as connection:
        revoked = connection.execute(
            select(role_bindings.c.status, role_bindings.c.revoked_at).where(
                role_bindings.c.binding_id == direct_binding.binding_id
            )
        ).one()
        assert revoked.status == "active"
        assert revoked.revoked_at is None
        assert (
            connection.scalar(
                select(workspaces.c.role_version).where(workspaces.c.workspace_id == workspace_id)
            )
            == before_disable.role_version + 1
        )

    second_owner = register(
        role_database,
        login_name="synthetic.cross.second.p1b04@example.com",
        display_name="合成第二空间所有者",
    )
    second_workspace = role_database.enterprise.create(
        context(second_owner), name="合成第二角色企业"
    )
    with role_database.engine.connect() as connection:
        second_membership_id = connection.execute(
            select(workspace_memberships.c.membership_id).where(
                workspace_memberships.c.workspace_id == second_workspace.workspace_id,
                workspace_memberships.c.account_id == second_owner.account_id,
            )
        ).scalar_one()
    with pytest.raises(IntegrityError), role_database.engine.begin() as connection:
        connection.execute(
            insert(role_bindings).values(
                binding_id=uuid4(),
                workspace_id=second_workspace.workspace_id,
                role_id=direct_role.role_id,
                scope_type="member",
                department_id=None,
                membership_id=second_membership_id,
                status="active",
                created_at=text("CURRENT_TIMESTAMP"),
                revoked_at=None,
                version=1,
            )
        )
