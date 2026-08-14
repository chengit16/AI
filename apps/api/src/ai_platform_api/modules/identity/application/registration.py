"""在单一事务中创建账号、默认个人空间、所有者关系、审计和事件。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.errors import (
    RegistrationConflictError,
    RegistrationValidationError,
)
from ai_platform_api.modules.identity.domain.models import IdentityReader, PasswordVerifier
from ai_platform_api.modules.identity.domain.registration import (
    AccountRegistration,
    DuplicateLoginNameError,
    RegistrationResult,
    RegistrationUnitOfWork,
)

LOGIN_NAME_PATTERN = re.compile(r"^[^\s\x00-\x1f\x7f]{3,255}$")


class RegistrationService:
    """创建账号及其唯一默认个人空间，并把审计与事件保持在同一事务。"""

    def __init__(
        self,
        repository: IdentityReader,
        unit_of_work: RegistrationUnitOfWork,
        passwords: PasswordVerifier,
    ) -> None:
        self._repository = repository
        self._unit_of_work = unit_of_work
        self._passwords = passwords

    def register(
        self,
        *,
        login_name: str,
        display_name: str,
        password: str,
        request_id: UUID,
        trace: TraceContext,
    ) -> RegistrationResult:
        """创建账号、个人空间、所有者成员、系统角色和默认权益并原子提交。"""

        # 1. 先规范化公开输入并完成冲突预检查，密码错误统一映射为注册校验失败。
        normalized_login = login_name.strip().casefold()
        normalized_display_name = display_name.strip()
        if (
            LOGIN_NAME_PATTERN.fullmatch(normalized_login) is None
            or not normalized_display_name
            or len(normalized_display_name) > 120
        ):
            raise RegistrationValidationError
        if self._repository.get_account_by_login(normalized_login) is not None:
            raise RegistrationConflictError
        try:
            password_hash = self._passwords.hash(password)
        except ValueError as error:
            raise RegistrationValidationError from error

        # 2. 一次生成账号、默认个人空间和随事务发布的审计/事件事实。
        account_id = uuid4()
        workspace_id = uuid4()
        now = datetime.now(UTC)
        workspace_name = f"{normalized_display_name[:115]}的个人空间"
        registration = AccountRegistration(
            account_id=account_id,
            login_name=normalized_login,
            display_name=normalized_display_name,
            password_hash=password_hash,
            personal_workspace_id=workspace_id,
            membership_id=uuid4(),
            personal_workspace_name=workspace_name,
            occurred_at=now,
        )
        event = IntegrationEvent(
            event_id=uuid4(),
            event_type="identity.account.registered",
            workspace_id=workspace_id,
            aggregate_id=account_id,
            aggregate_version=1,
            occurred_at=now,
            trace_id=trace.trace_id,
            traceparent=trace.traceparent,
            actor_id=account_id,
            user_id=account_id,
            request_id=request_id,
            payload={"personal_workspace_id": str(workspace_id)},
        )
        audit = AuditRecord(
            audit_id=uuid4(),
            workspace_id=workspace_id,
            actor_id=account_id,
            user_id=account_id,
            action="identity.account.register",
            resource_type="account",
            resource_id=account_id,
            outcome="succeeded",
            occurred_at=now,
            request_id=request_id,
            trace_id=trace.trace_id,
            traceparent=trace.traceparent,
            attributes={"workspace_type": "personal"},
        )
        # 3. 所有注册事实在同一事务提交；数据库唯一约束负责关闭并发同名竞态。
        try:
            with self._unit_of_work:
                self._unit_of_work.registrations.add(registration)
                self._unit_of_work.audit.add(audit)
                self._unit_of_work.outbox.add(event)
                self._unit_of_work.commit()
        except DuplicateLoginNameError as error:
            # 预检查只优化常见冲突；数据库唯一约束负责关闭并发注册竞态。
            raise RegistrationConflictError from error
        return RegistrationResult(account_id=account_id, personal_workspace_id=workspace_id)
