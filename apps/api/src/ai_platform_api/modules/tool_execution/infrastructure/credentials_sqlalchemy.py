"""用 PostgreSQL 约束工具凭证版本，并在最窄调用边缘完成解密。"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import TypeVar, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord
from ai_platform_backend.integration.sqlalchemy import SqlAlchemyAuditWriter
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.security import EncryptedSecret, EnvelopeSecretCipher
from ai_platform_api.modules.tool_execution.domain.credentials import (
    ToolCredential,
    ToolCredentialBinding,
    ToolCredentialStatus,
    tool_credential_associated_data,
)
from ai_platform_api.modules.tool_execution.domain.errors import (
    ToolAdapterUnavailableError,
    ToolCredentialExposureDetectedError,
    ToolCredentialUnavailableError,
    ToolExecutionDeniedError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt
from ai_platform_api.persistence.tables import (
    agent_tool_definitions,
    tool_attempts,
    tool_calls,
    tool_credentials,
    tool_runs,
    tool_steps,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]
T = TypeVar("T")


class SqlAlchemyToolCredentialStore:
    """集中实现所有者管理、一次性引用绑定和受控明文调用边缘。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        cipher: EnvelopeSecretCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    def create(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        plaintext: str,
        created_at: datetime,
    ) -> ToolCredential:
        """只允许所有者为尚无历史的合成写工具创建首个凭证。"""

        try:
            with self._session_factory() as session, session.begin():
                # 未授权请求不能触发主密钥读取，所有者和定义校验先于加密。
                account_id = _require_owner(session, context)
                _require_managed_definition(session, tool_id, tool_version)
                existing_count = session.scalar(
                    select(func.count())
                    .select_from(tool_credentials)
                    .where(
                        tool_credentials.c.workspace_id == context.workspace_id,
                        tool_credentials.c.tool_id == tool_id,
                        tool_credentials.c.tool_version == tool_version,
                    )
                )
                if existing_count:
                    raise ToolCredentialUnavailableError
                credential = self._new_credential(
                    workspace_id=context.workspace_id,
                    tool_id=tool_id,
                    tool_version=tool_version,
                    credential_version=1,
                    plaintext=plaintext,
                    account_id=account_id,
                    occurred_at=created_at,
                )
                session.execute(insert(tool_credentials).values(**_credential_values(credential)))
                _record_audit(session, context, credential, "tool.credential.created")
                return credential
        except (IntegrityError, DBAPIError) as error:
            raise ToolCredentialUnavailableError from error

    def rotate(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        plaintext: str,
        rotated_at: datetime,
    ) -> ToolCredential:
        """锁定活动版本后先撤销旧引用，再原子创建新引用。"""

        try:
            with self._session_factory() as session, session.begin():
                # 1. 事务内复核所有者和定义，并锁定唯一活动版本作为轮换串行化点。
                account_id = _require_owner(session, context)
                _require_managed_definition(session, tool_id, tool_version)
                current = _active_credential_row(
                    session,
                    context.workspace_id,
                    tool_id,
                    tool_version,
                    for_update=True,
                )
                if current is None:
                    raise ToolCredentialUnavailableError

                # 2. 旧行写锁等待调用边缘共享锁结束，再连续分配版本并原子替换活动引用。
                next_version = (
                    int(
                        session.scalar(
                            select(func.max(tool_credentials.c.credential_version)).where(
                                tool_credentials.c.workspace_id == context.workspace_id,
                                tool_credentials.c.tool_id == tool_id,
                                tool_credentials.c.tool_version == tool_version,
                            )
                        )
                        or 0
                    )
                    + 1
                )
                session.execute(
                    update(tool_credentials)
                    .where(tool_credentials.c.credential_id == current["credential_id"])
                    .values(
                        status="revoked",
                        revoked_by_account_id=account_id,
                        revoked_at=rotated_at,
                    )
                )
                credential = self._new_credential(
                    workspace_id=context.workspace_id,
                    tool_id=tool_id,
                    tool_version=tool_version,
                    credential_version=next_version,
                    plaintext=plaintext,
                    account_id=account_id,
                    occurred_at=rotated_at,
                )
                session.execute(insert(tool_credentials).values(**_credential_values(credential)))
                _record_audit(session, context, credential, "tool.credential.rotated")
                return credential
        except (IntegrityError, DBAPIError) as error:
            raise ToolCredentialUnavailableError from error

    def revoke(
        self,
        context: RequestContext,
        *,
        tool_id: UUID,
        tool_version: int,
        revoked_at: datetime,
    ) -> ToolCredential:
        """撤销活动版本；历史密文保留用于审计，但永远不能再次激活。"""

        try:
            with self._session_factory() as session, session.begin():
                account_id = _require_owner(session, context)
                _require_managed_definition(session, tool_id, tool_version)
                current = _active_credential_row(
                    session,
                    context.workspace_id,
                    tool_id,
                    tool_version,
                    for_update=True,
                )
                if current is None:
                    raise ToolCredentialUnavailableError
                revoked = (
                    session.execute(
                        update(tool_credentials)
                        .where(tool_credentials.c.credential_id == current["credential_id"])
                        .values(
                            status="revoked",
                            revoked_by_account_id=account_id,
                            revoked_at=revoked_at,
                        )
                        .returning(tool_credentials)
                    )
                    .mappings()
                    .one()
                )
                credential = _credential(revoked)
                _record_audit(session, context, credential, "tool.credential.revoked")
                return credential
        except (IntegrityError, DBAPIError) as error:
            raise ToolCredentialUnavailableError from error

    def authorize_call(
        self,
        claim: ClaimedToolAttempt,
        *,
        authorized_at: datetime,
    ) -> ToolCredentialBinding:
        """以当前租约和活动凭证为条件原子完成首次引用绑定。"""

        try:
            with self._session_factory() as session, session.begin():
                row = _claim_row(session, claim, for_update=True)
                _require_claim(row, claim, authorized_at, expected_call_state=None)
                if row["call_state"] == "authorized":
                    return _existing_binding(session, row, claim)
                if row["call_state"] != "proposed":
                    raise ToolRunConflictError
                current = _active_credential_row(
                    session,
                    claim.workspace_id,
                    claim.tool_id,
                    claim.tool_version,
                    for_update=False,
                )
                if current is None:
                    raise ToolCredentialUnavailableError
                session.execute(
                    update(tool_calls)
                    .where(tool_calls.c.tool_call_id == claim.tool_call_id)
                    .values(
                        credential_ref=current["credential_ref"],
                        state="authorized",
                        updated_at=authorized_at,
                    )
                )
                return ToolCredentialBinding(
                    claim.tool_call_id,
                    cast(str, current["credential_ref"]),
                    cast(int, current["credential_version"]),
                )
        except (IntegrityError, DBAPIError) as error:
            raise ToolCredentialUnavailableError from error

    def invoke(
        self,
        claim: ClaimedToolAttempt,
        operation: Callable[[str], T],
        *,
        invoked_at: datetime,
    ) -> T:
        """在活动凭证共享锁内解密并调用 Adapter，异常只传播稳定错误。"""

        with self._session_factory() as session, session.begin():
            row = _claim_row(session, claim, for_update=False)
            _require_claim(row, claim, invoked_at, expected_call_state="executing")
            credential = _bound_active_credential(session, row, claim)
            try:
                plaintext = self._cipher.decrypt(
                    credential.envelope,
                    associated_data=_associated_data(credential),
                )
            except Exception:
                raise ToolCredentialUnavailableError from None

            # 明文生命周期被限制在这个 try/finally 内；返回值和异常都在离开边界前检查。
            try:
                result = operation(plaintext)
                if _contains_plaintext(result, plaintext):
                    raise ToolCredentialExposureDetectedError
                return result
            except ToolCredentialExposureDetectedError:
                raise
            except Exception as error:
                if _exception_contains_plaintext(error, plaintext):
                    raise ToolCredentialExposureDetectedError from None
                raise ToolAdapterUnavailableError from None
            finally:
                plaintext = ""

    def _new_credential(
        self,
        *,
        workspace_id: UUID,
        tool_id: UUID,
        tool_version: int,
        credential_version: int,
        plaintext: str,
        account_id: UUID,
        occurred_at: datetime,
    ) -> ToolCredential:
        """先生成不可猜测引用，再用全部身份字段作为 AES-GCM 关联数据。"""

        credential_id = uuid4()
        credential_ref = f"cred_{secrets.token_hex(24)}"
        associated_data = tool_credential_associated_data(
            workspace_id=workspace_id,
            tool_id=tool_id,
            tool_version=tool_version,
            credential_id=credential_id,
            credential_ref=credential_ref,
            credential_version=credential_version,
        )
        envelope = self._cipher.encrypt(plaintext, associated_data=associated_data)
        return ToolCredential(
            credential_id=credential_id,
            credential_ref=credential_ref,
            workspace_id=workspace_id,
            tool_id=tool_id,
            tool_version=tool_version,
            credential_version=credential_version,
            envelope=envelope,
            status="active",
            created_by_account_id=account_id,
            created_at=occurred_at,
            revoked_by_account_id=None,
            revoked_at=None,
        )


def _require_owner(session: Session, context: RequestContext) -> UUID:
    """复核活动 Workspace 的活动所有者成员，个人和企业使用同一事实语义。"""

    account_id = context.user_id
    if (
        context.authentication_method != "browser_session"
        or account_id is None
        or context.actor_id != account_id
    ):
        raise ToolExecutionDeniedError
    owner = session.scalar(
        select(workspace_memberships.c.account_id)
        .join(workspaces, workspaces.c.workspace_id == workspace_memberships.c.workspace_id)
        .where(
            workspaces.c.workspace_id == context.workspace_id,
            workspaces.c.status == "active",
            workspace_memberships.c.account_id == account_id,
            workspace_memberships.c.membership_type == "owner",
            workspace_memberships.c.status == "active",
        )
    )
    if owner != account_id:
        raise ToolExecutionDeniedError
    return account_id


def _require_managed_definition(session: Session, tool_id: UUID, tool_version: int) -> None:
    """阶段 4 只允许需要引用的合成内部写工具拥有工具凭证。"""

    exists = session.scalar(
        select(agent_tool_definitions.c.tool_id).where(
            agent_tool_definitions.c.tool_id == tool_id,
            agent_tool_definitions.c.tool_version == tool_version,
            agent_tool_definitions.c.status == "active",
            agent_tool_definitions.c.access_mode == "write",
            agent_tool_definitions.c.adapter_kind == "synthetic_internal_write",
            agent_tool_definitions.c.credential_requirement == "credential_ref",
            agent_tool_definitions.c.synthetic.is_(True),
        )
    )
    if exists is None:
        raise ToolCredentialUnavailableError


def _active_credential_row(
    session: Session,
    workspace_id: UUID,
    tool_id: UUID,
    tool_version: int,
    *,
    for_update: bool,
) -> RowMapping | None:
    statement = select(tool_credentials).where(
        tool_credentials.c.workspace_id == workspace_id,
        tool_credentials.c.tool_id == tool_id,
        tool_credentials.c.tool_version == tool_version,
        tool_credentials.c.status == "active",
    )
    if for_update:
        statement = statement.with_for_update()
    return session.execute(statement).mappings().one_or_none()


def _claim_row(
    session: Session,
    claim: ClaimedToolAttempt,
    *,
    for_update: bool,
) -> RowMapping:
    """读取完整租约和 ToolCall 绑定，调用方决定是否需要阻止并发状态推进。"""

    statement = (
        select(
            tool_calls,
            tool_calls.c.state.label("call_state"),
            tool_attempts.c.state.label("attempt_state"),
            tool_attempts.c.worker_id,
            tool_attempts.c.lease_generation,
            tool_attempts.c.lease_expires_at,
            tool_steps.c.current_attempt_no,
            tool_steps.c.state.label("step_state"),
            tool_runs.c.state.label("run_state"),
            tool_runs.c.cancel_requested_at,
        )
        .join(tool_attempts, tool_attempts.c.attempt_id == tool_calls.c.attempt_id)
        .join(tool_steps, tool_steps.c.step_id == tool_calls.c.step_id)
        .join(tool_runs, tool_runs.c.run_id == tool_calls.c.run_id)
        .where(tool_calls.c.tool_call_id == claim.tool_call_id)
    )
    if for_update:
        statement = statement.with_for_update()
    row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise ToolRunConflictError
    return row


def _require_claim(
    row: RowMapping,
    claim: ClaimedToolAttempt,
    occurred_at: datetime,
    *,
    expected_call_state: str | None,
) -> None:
    """逐字段复核租约身份，避免仅凭可猜测 ID 注入其他空间凭证。"""

    if (
        row["attempt_id"] != claim.attempt_id
        or row["run_id"] != claim.run_id
        or row["step_id"] != claim.step_id
        or row["workspace_id"] != claim.workspace_id
        or row["tool_id"] != claim.tool_id
        or row["tool_version"] != claim.tool_version
        or row["canonical_arguments_hash"] != claim.canonical_arguments_hash
        or row["worker_id"] != claim.worker_id
        or row["lease_generation"] != claim.lease_generation
        or row["current_attempt_no"] != claim.attempt_no
        or row["attempt_state"] != "executing"
        or row["step_state"] != "running"
        or row["run_state"] != "running"
        or row["cancel_requested_at"] is not None
        or occurred_at >= cast(datetime, row["lease_expires_at"])
        or claim.lease_expires_at != row["lease_expires_at"]
        or (expected_call_state is not None and row["call_state"] != expected_call_state)
    ):
        raise ToolRunConflictError


def _existing_binding(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
) -> ToolCredentialBinding:
    credential = _bound_active_credential(session, row, claim)
    return ToolCredentialBinding(
        claim.tool_call_id,
        credential.credential_ref,
        credential.credential_version,
    )


def _bound_active_credential(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
) -> ToolCredential:
    """以共享锁固定活动版本，使本次回调完成前轮换或撤销不能提交。"""

    credential_ref = row["credential_ref"]
    if not isinstance(credential_ref, str):
        raise ToolCredentialUnavailableError
    credential_row = (
        session.execute(
            select(tool_credentials)
            .where(
                tool_credentials.c.credential_ref == credential_ref,
                tool_credentials.c.workspace_id == claim.workspace_id,
                tool_credentials.c.tool_id == claim.tool_id,
                tool_credentials.c.tool_version == claim.tool_version,
                tool_credentials.c.status == "active",
            )
            .with_for_update(read=True)
        )
        .mappings()
        .one_or_none()
    )
    if credential_row is None:
        raise ToolCredentialUnavailableError
    return _credential(credential_row)


def _credential(row: RowMapping) -> ToolCredential:
    return ToolCredential(
        credential_id=cast(UUID, row["credential_id"]),
        credential_ref=cast(str, row["credential_ref"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        credential_version=cast(int, row["credential_version"]),
        envelope=EncryptedSecret(
            key_version=cast(int, row["master_key_version"]),
            encrypted_data_key=bytes(row["encrypted_data_key"]),
            data_key_nonce=bytes(row["data_key_nonce"]),
            ciphertext=bytes(row["ciphertext"]),
            data_nonce=bytes(row["data_nonce"]),
            last_four=cast(str, row["last_four"]),
        ),
        status=cast(ToolCredentialStatus, row["status"]),
        created_by_account_id=cast(UUID, row["created_by_account_id"]),
        created_at=cast(datetime, row["created_at"]),
        revoked_by_account_id=cast(UUID | None, row["revoked_by_account_id"]),
        revoked_at=cast(datetime | None, row["revoked_at"]),
    )


def _credential_values(credential: ToolCredential) -> dict[str, object]:
    envelope = credential.envelope
    return {
        "credential_id": credential.credential_id,
        "credential_ref": credential.credential_ref,
        "workspace_id": credential.workspace_id,
        "tool_id": credential.tool_id,
        "tool_version": credential.tool_version,
        "credential_version": credential.credential_version,
        "master_key_version": envelope.key_version,
        "encrypted_data_key": envelope.encrypted_data_key,
        "data_key_nonce": envelope.data_key_nonce,
        "ciphertext": envelope.ciphertext,
        "data_nonce": envelope.data_nonce,
        "last_four": envelope.last_four,
        "status": credential.status,
        "created_by_account_id": credential.created_by_account_id,
        "created_at": credential.created_at,
        "revoked_by_account_id": credential.revoked_by_account_id,
        "revoked_at": credential.revoked_at,
    }


def _associated_data(credential: ToolCredential) -> bytes:
    return tool_credential_associated_data(
        workspace_id=credential.workspace_id,
        tool_id=credential.tool_id,
        tool_version=credential.tool_version,
        credential_id=credential.credential_id,
        credential_ref=credential.credential_ref,
        credential_version=credential.credential_version,
    )


def _record_audit(
    session: Session,
    context: RequestContext,
    credential: ToolCredential,
    action: str,
) -> None:
    """审计只保存治理版本，不保存引用、末四位、密文或明文。"""

    SqlAlchemyAuditWriter(session).add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="tool_credential",
            resource_id=credential.credential_id,
            outcome="succeeded",
            occurred_at=credential.revoked_at or credential.created_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes={
                "credential_version": credential.credential_version,
                "status": credential.status,
                "tool_id": str(credential.tool_id),
                "tool_version": credential.tool_version,
            },
        )
    )


def _contains_plaintext(value: object, plaintext: str, seen: set[int] | None = None) -> bool:
    """检查常见 Adapter 结果容器，阻止凭证明文作为结果或字段名越界。"""

    if isinstance(value, str):
        return plaintext in value
    if isinstance(value, bytes):
        return plaintext.encode() in value
    visited = seen if seen is not None else set()
    identity = id(value)
    if identity in visited:
        return False
    visited.add(identity)
    if isinstance(value, Mapping):
        return any(
            _contains_plaintext(item, plaintext, visited) for pair in value.items() for item in pair
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_plaintext(item, plaintext, visited) for item in value)
    return False


def _exception_contains_plaintext(error: Exception, plaintext: str) -> bool:
    """异常字符串化本身失败时也不传播原异常对象或其潜在敏感上下文。"""

    try:
        return plaintext in str(error)
    except Exception:
        return False


__all__ = ["SqlAlchemyToolCredentialStore"]
