"""提供 P5-02 真实 PostgreSQL 测试共用的授权 Harness。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.quality.application.service import QualitySampleService
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

TRACE = TraceContext("5" * 32, "2" * 16)


@dataclass(frozen=True)
class RegisteredAccount:
    """保存测试账号与默认个人空间标识。"""

    account_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class QualityHarness:
    """集中持有临时 Schema 的注册服务和质量样本入口。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    quality: QualitySampleService


class QualityRegistrationHarness(Protocol):
    """描述跨质量节点注册合成账号所需的最小 Harness 接口。"""

    @property
    def registration(self) -> RegistrationService:
        """返回临时 Schema 使用的账号注册服务。"""

        ...


def register(harness: QualityRegistrationHarness, identity: str) -> RegisteredAccount:
    """注册全合成账号，并返回服务端创建的可信个人空间。"""

    result = harness.registration.register(
        login_name=f"synthetic.quality.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成质量样本用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def authorized_context(
    account: RegisteredAccount,
    permission_code: str,
) -> RequestContext:
    """构造完整且可追溯的合成授权投影，不绕过 Application 校验。"""

    return replace(
        RequestContext.trusted(
            actor_id=account.account_id,
            user_id=account.account_id,
            workspace_id=account.workspace_id,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_permission_code=permission_code,
        authorized_policy_decision_id=uuid4(),
        authorized_policy_version=1,
        authorized_workspace=True,
        authorized_maximum_security_level="INTERNAL",
    )
