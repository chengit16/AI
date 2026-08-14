"""验证身份模块共享 UoW 在并发请求间隔离 Session 状态。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from threading import Barrier, Lock
from typing import Protocol, cast

import pytest
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.roles_sqlalchemy import (
    SqlAlchemyRoleUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityUnitOfWork,
    SqlAlchemyRegistrationUnitOfWork,
)
from sqlalchemy.orm import Session


class SessionBackedResource(Protocol):
    _session: Session


class ConcurrentSession:
    def __init__(self) -> None:
        self.closed = False

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


UnitOfWorkFactory = Callable[[Callable[[], Session]], object]


@pytest.mark.parametrize(
    ("unit_of_work_factory", "resource_name"),
    [
        (SqlAlchemyIdentityUnitOfWork, "api_keys"),
        (SqlAlchemyRegistrationUnitOfWork, "registrations"),
        (SqlAlchemyEnterpriseUnitOfWork, "enterprise"),
        (SqlAlchemyEntitlementUnitOfWork, "entitlements"),
        (SqlAlchemyOrganizationUnitOfWork, "organization"),
        (SqlAlchemyRoleUnitOfWork, "roles"),
    ],
)
def test_shared_unit_of_work_isolates_overlapping_requests(
    unit_of_work_factory: UnitOfWorkFactory,
    resource_name: str,
) -> None:
    sessions: list[ConcurrentSession] = []
    sessions_lock = Lock()
    barrier = Barrier(2)

    def session_factory() -> Session:
        session = ConcurrentSession()
        with sessions_lock:
            sessions.append(session)
        return cast(Session, session)

    unit_of_work = unit_of_work_factory(session_factory)
    context_manager = cast(AbstractContextManager[object], unit_of_work)

    def use_transaction() -> tuple[int, int]:
        with context_manager:
            resource = cast(SessionBackedResource, getattr(unit_of_work, resource_name))
            barrier.wait(timeout=5)
            # UoW 由容器复用；另一请求进入后仍须解析到本请求的资源与 Session。
            assert getattr(unit_of_work, resource_name) is resource
            barrier.wait(timeout=5)
            return id(resource), id(resource._session)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: use_transaction(), range(2)))

    assert results[0][0] != results[1][0]
    assert results[0][1] != results[1][1]
    assert len(sessions) == 2
    assert all(session.closed for session in sessions)
