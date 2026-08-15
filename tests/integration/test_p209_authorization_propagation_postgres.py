"""验证 P2-09 真实 PostgreSQL/Valkey 撤权传播与旧缓存失败关闭。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.grants import RolePermissionService
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.policy import (
    AuthorizationSurface,
    PolicyDecision,
    PolicyRequest,
    ResourceReference,
)
from ai_platform_api.modules.authorization.infrastructure.policy_version import (
    PolicyVersionClient,
    ValkeyPolicyVersionGate,
)
from ai_platform_api.modules.authorization.infrastructure.sqlalchemy import (
    SqlAlchemyPolicyGrantRepository,
    SqlAlchemyRolePermissionUnitOfWork,
)
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
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
from ai_platform_api.persistence.tables import workspaces
from alembic import command
from alembic.config import Config
from redis import Redis
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/0"
TRACE = TraceContext.continue_from("00-a123456789abcdef0123456789abcdef-a123456789abcdef-01")
SURFACES: tuple[AuthorizationSurface, ...] = (
    "menu",
    "api",
    "retrieval",
    "field_projection",
)


@dataclass(frozen=True)
class Account:
    """保存演练所需的最小合成账号事实。"""

    account_id: UUID
    personal_workspace_id: UUID
    login_name: str


@dataclass(frozen=True)
class PropagationHarness:
    """集中持有演练服务、数据库和版本见证客户端。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    roles: RoleService
    permissions: RolePermissionService
    policy: RbacPolicyDecisionPoint
    gate: ValkeyPolicyVersionGate
    valkey: Redis


@pytest.fixture(scope="module")
def propagation_database() -> Iterator[PropagationHarness]:
    """在隔离 Schema 中装配真实授权服务，并复用本地 Valkey。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    schema = f"p209_test_{uuid4().hex}"
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
    valkey = Redis.from_url(valkey_url, decode_responses=True)
    valkey.ping()
    gate = ValkeyPolicyVersionGate(client=cast("PolicyVersionClient", valkey))
    reader = SqlAlchemyIdentityReader(sessions)
    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    field_registry = load_field_policy_registry(
        ROOT / "contracts/authorization/field-policy-registry.v1.json"
    )
    try:
        yield PropagationHarness(
            engine=engine,
            sessions=sessions,
            registration=RegistrationService(
                reader,
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            enterprise=EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
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
                gate,
            ),
            gate=gate,
            valkey=valkey,
        )
    finally:
        gate.close()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _register(harness: PropagationHarness, name: str) -> Account:
    login_name = f"synthetic.p209.{name}.{uuid4().hex}@example.com"
    result = harness.registration.register(
        login_name=login_name,
        display_name=f"合成{name}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return Account(result.account_id, result.personal_workspace_id, login_name)


def _context(account: Account, workspace_id: UUID | None = None) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id or account.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def _request(context: RequestContext, surface: AuthorizationSurface) -> PolicyRequest:
    return PolicyRequest(
        context=context,
        permission_code="knowledge.document.read",
        resource=ResourceReference(
            "document",
            context.workspace_id,
            context.workspace_id,
            {"risk_level": "high"},
        ),
        surface=surface,
    )


def _nearest_rank_p99(values: list[float]) -> float:
    """按 P2-01 固定的 nearest-rank 口径返回 20 个样本的 P99。"""

    assert len(values) >= 20
    ordered = sorted(values)
    rank = max(1, (99 * len(ordered) + 99) // 100)
    return ordered[rank - 1]


def test_dropped_invalidation_rejects_all_surfaces_within_five_seconds(
    propagation_database: PropagationHarness,
) -> None:
    # 1. 为合成成员建立仅由自定义角色提供的知识权限，并用首次决策初始化版本见证。
    owner = _register(propagation_database, "owner")
    member = _register(propagation_database, "member")
    workspace = propagation_database.enterprise.create(_context(owner), name="合成传播企业")
    owner_context = _context(owner, workspace.workspace_id)
    invitation = propagation_database.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name=member.login_name,
    )
    propagation_database.enterprise.accept_invitation(
        _context(member),
        invitation_id=invitation.invitation_id,
    )
    member_context = _context(member, workspace.workspace_id)
    role = propagation_database.roles.create(
        owner_context,
        workspace_id=workspace.workspace_id,
        role_key="synthetic_knowledge_reader",
        name="合成知识读取者",
    )
    binding = propagation_database.roles.bind(
        owner_context,
        workspace_id=workspace.workspace_id,
        role_id=role.role_id,
        scope_type="member",
        department_id=None,
        target_account_id=member.account_id,
    )
    propagation_database.permissions.replace(
        owner_context,
        workspace_id=workspace.workspace_id,
        role_id=role.role_id,
        entries=(
            (
                "knowledge.document.read",
                "workspace",
                frozenset(),
                frozenset(),
                "INTERNAL",
                frozenset(),
            ),
        ),
    )
    initial = propagation_database.policy.decide(_request(member_context, "api"))
    assert initial.allowed is True
    cache_key = f"authorization-policy-version:v1:{workspace.workspace_id}"
    old_version = int(cast(str, propagation_database.valkey.get(cache_key)))

    # 2. 撤销绑定后反复写回旧见证，模拟跨实例失效通知持续丢失而旧缓存仍存在。
    propagation_database.roles.revoke(
        owner_context,
        workspace_id=workspace.workspace_id,
        binding_id=binding.binding_id,
    )
    with propagation_database.sessions() as session:
        current_version = session.scalar(
            select(workspaces.c.role_version).where(
                workspaces.c.workspace_id == workspace.workspace_id
            )
        )
    assert current_version is not None and current_version > old_version

    durations: list[float] = []
    decisions: list[PolicyDecision] = []
    for sample_index in range(20):
        propagation_database.valkey.set(cache_key, old_version)
        surface = SURFACES[sample_index % len(SURFACES)]
        started = monotonic()
        decision = propagation_database.policy.decide(_request(member_context, surface))
        durations.append(monotonic() - started)
        decisions.append(decision)

    # 3. 四个表面均先以可重试策略错误失败关闭，结果不包含请求目标或合成受限正文。
    assert {decision.reason for decision in decisions} == {"policy_unavailable"}
    assert all(
        not decision.allowed and decision.policy_version == current_version
        for decision in decisions
    )
    assert _nearest_rank_p99(durations) <= 5
    assert "SYNTHETIC_RESTRICTED_CONTENT" not in repr(decisions)
    assert int(cast(str, propagation_database.valkey.get(cache_key))) == current_version

    # 4. 见证修复后仍按新 PostgreSQL 事实拒绝，不会因为缓存恢复而重新获得旧权限。
    final = propagation_database.policy.decide(_request(member_context, "retrieval"))
    assert final.allowed is False
    assert final.reason == "permission_not_granted"
    propagation_database.valkey.delete(cache_key)
