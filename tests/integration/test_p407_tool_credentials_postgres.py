"""验证 P4-07 工具凭证授权、轮换、调用边缘和 PostgreSQL 防绕过闭环。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.security import EncryptedSecret, EnvelopeSecretCipher, MasterKeyFile
from ai_platform_api.modules.authorization.domain.policy import PolicyDecision, ResourceScope
from ai_platform_api.modules.tool_execution.application.confirmations import ToolConfirmationService
from ai_platform_api.modules.tool_execution.application.credentials import ToolCredentialService
from ai_platform_api.modules.tool_execution.application.definitions import parse_tool_definition
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolCredentialExposureDetectedError,
    ToolCredentialUnavailableError,
    ToolExecutionDeniedError,
)
from ai_platform_api.modules.tool_execution.application.tasks import ToolTaskService
from ai_platform_api.modules.tool_execution.domain.catalog import ToolDefinition
from ai_platform_api.modules.tool_execution.domain.credentials import (
    tool_credential_associated_data,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt
from ai_platform_api.modules.tool_execution.infrastructure.confirmations_sqlalchemy import (
    SqlAlchemyToolConfirmationStore,
)
from ai_platform_api.modules.tool_execution.infrastructure.credentials_sqlalchemy import (
    SqlAlchemyToolCredentialStore,
)
from ai_platform_api.modules.tool_execution.infrastructure.tasks_sqlalchemy import (
    SqlAlchemyToolTaskStore,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeCommand
from ai_platform_api.persistence.tables import (
    agent_tool_definitions,
    audit_records,
    outbox_events,
    tool_calls,
    tool_credentials,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError

from scripts.rotate_master_key import rewrap_tool_credentials
from tests.integration.test_p304_agent_evaluation_postgres import RegisteredAccount, context
from tests.integration.test_p305_agent_approval_postgres import (
    ApprovalHarness,
    add_enterprise_member,
    approval_harness,
    register,
)
from tests.integration.test_p406_tool_confirmation_postgres import (
    RUN_BUDGET,
    SCHEMA_DOCUMENT,
    _routed_approvals,
    _seed_service,
)

CREDENTIAL_TOOL_ID = UUID("a7000000-0000-4000-8000-000000000407")
CREDENTIAL_PERMISSION = "synthetic.record.write"
MASTER_KEY_SECRET = b"p" * 32
FIRST_SECRET = "synthetic-p407-secret-first"
SECOND_SECRET = "synthetic-p407-secret-second"
ROOT = Path(__file__).parents[2]


def _definition(version: int) -> ToolDefinition:
    return parse_tool_definition(
        {
            "tool_id": str(CREDENTIAL_TOOL_ID),
            "tool_version": version,
            "tool_key": "synthetic.credential_write",
            "display_name": f"合成凭证写入 V{version}",
            "description": "仅用于验证工具凭证边界, 不执行真实外部副作用。",
            "access_mode": "write",
            "risk_level": "high",
            "adapter_kind": "synthetic_internal_write",
            "input_schema_document": SCHEMA_DOCUMENT,
            "output_schema_document": SCHEMA_DOCUMENT,
            "permission_code": CREDENTIAL_PERMISSION,
            "credential_requirement": "credential_ref",
            "timeout_seconds": 30,
            "retry_mode": "idempotent_write",
            "status": "active",
            "synthetic": True,
        },
        created_at=datetime(2026, 8, 16, 14, version, tzinfo=UTC),
    )


CREDENTIAL_DEFINITION_V1 = _definition(1)
CREDENTIAL_DEFINITION_V2 = _definition(2)


class CredentialWriteCatalog:
    """为 V1 合成凭证工具返回确定性允许结论，不访问真实连接器。"""

    def authorize_available_tool(
        self,
        request_context: RequestContext,
        *,
        workspace_id: UUID,
        tool_id: UUID,
        tool_version: int,
        resource_id: UUID | None = None,
    ) -> tuple[ToolDefinition, PolicyDecision]:
        del resource_id
        if (
            request_context.workspace_id != workspace_id
            or tool_id != CREDENTIAL_TOOL_ID
            or tool_version != 1
        ):
            raise AssertionError("合成凭证 Catalog 收到越界工具请求")
        return CREDENTIAL_DEFINITION_V1, PolicyDecision(
            uuid4(),
            "allow",
            CREDENTIAL_PERMISSION,
            workspace_id,
            ResourceScope(workspace=True),
            frozenset(),
            1,
            0,
            "synthetic_allow",
            "CONFIDENTIAL",
        )


@dataclass(frozen=True)
class CredentialDatabase:
    """集中保存共享 Schema、密钥和工具凭证服务。"""

    harness: ApprovalHarness
    key_path: Path
    store: SqlAlchemyToolCredentialStore
    service: ToolCredentialService


@pytest.fixture(scope="module")
def credential_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CredentialDatabase]:
    """迁移到 head，登记两个不可变合成工具版本并配置测试主密钥。"""

    key_path = tmp_path_factory.mktemp("p407-keys") / "master.key"
    key_path.write_bytes(MASTER_KEY_SECRET)
    os.chmod(key_path, 0o600)
    for harness in approval_harness():
        with harness.sessions.begin() as session:
            session.execute(
                insert(agent_tool_definitions),
                (asdict(CREDENTIAL_DEFINITION_V1), asdict(CREDENTIAL_DEFINITION_V2)),
            )
        store = SqlAlchemyToolCredentialStore(
            harness.sessions,
            EnvelopeSecretCipher(MasterKeyFile(str(key_path), 1)),
        )
        yield CredentialDatabase(harness, key_path, store, ToolCredentialService(store, store))


def _ready_claim(
    database: CredentialDatabase,
    owner: RegisteredAccount,
    suffix: str,
) -> tuple[RequestContext, ToolTaskService, ClaimedToolAttempt]:
    """完成个人确认和当前 PDP 复核，再领取一个 V1 合成写调用。"""

    harness = database.harness
    request_context = context(owner)
    now = datetime.now(UTC)
    service_id, release_id = _seed_service(harness, owner, f"p407-{suffix}", now)
    tasks = ToolTaskService(SqlAlchemyToolTaskStore(harness.sessions))
    run = tasks.create_run(
        request_context,
        service_id=service_id,
        agent_release_id=release_id,
        idempotency_key=f"synthetic-p407-run-{suffix}",
        budget=RUN_BUDGET,
        created_at=now,
    )
    tasks.transition_run(request_context, run.run_id, "planning", occurred_at=now)
    step = tasks.append_step(
        request_context,
        run.run_id,
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        canonical_arguments_hash=hashlib.sha256(suffix.encode()).hexdigest(),
        created_at=now,
    )
    tasks.transition_step(request_context, step.step_id, "policy_checking", occurred_at=now)
    tasks.transition_run(request_context, run.run_id, "running", occurred_at=now)

    # 批准只形成历史事实；必须调用 resume 重新执行当前 PDP 后才能领取 Step。
    approvals = _routed_approvals(harness)
    confirmation_store = SqlAlchemyToolConfirmationStore(harness.sessions)
    confirmations = ToolConfirmationService(
        confirmation_store,
        CredentialWriteCatalog(),
        approvals,
    )
    requested = confirmations.request(
        request_context,
        step_id=step.step_id,
        idempotency_key=f"synthetic-p407-confirm-{suffix}",
    )
    approvals.act(
        request_context,
        approval_instance_id=requested.confirmation.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            f"synthetic-p407-approve-{suffix}",
        ),
    )
    approved = confirmation_store.get_confirmation(
        owner.workspace_id,
        requested.confirmation.confirmation_id,
    )
    assert approved is not None
    confirmations.resume(
        request_context,
        confirmation_id=approved.confirmation_id,
        expected_binding=approved.binding,
    )
    claim = tasks.claim_next(
        worker_id=f"synthetic-p407-worker-{suffix}",
        now=datetime.now(UTC),
        lease_seconds=300,
    )
    assert claim is not None
    assert tasks.begin_attempt(claim, started_at=datetime.now(UTC))
    return request_context, tasks, claim


def _advance_to_executing(
    database: CredentialDatabase,
    tasks: ToolTaskService,
    claim: ClaimedToolAttempt,
) -> None:
    database.service.authorize_call(claim)
    assert tasks.transition_call(claim, "confirmed", occurred_at=datetime.now(UTC))
    assert tasks.transition_call(claim, "executing", occurred_at=datetime.now(UTC))


def test_personal_and_enterprise_owner_manage_credentials_but_member_cannot(
    credential_database: CredentialDatabase,
) -> None:
    """个人与企业所有者共享管理语义，企业普通成员不能借当前空间创建凭证。"""

    harness = credential_database.harness
    personal_owner = register(harness, "p407-personal-owner")
    personal = credential_database.service.create(
        context(personal_owner),
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=FIRST_SECRET,
    )
    assert personal.status == "active" and personal.envelope.last_four == "irst"

    enterprise_owner = register(harness, "p407-enterprise-owner")
    summary = harness.enterprise.create(context(enterprise_owner), name="合成 P4-07 企业空间")
    enterprise_owner = replace(enterprise_owner, workspace_id=summary.workspace_id)
    enterprise = credential_database.service.create(
        context(enterprise_owner),
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=FIRST_SECRET,
    )
    assert enterprise.workspace_id == summary.workspace_id

    member = register(harness, "p407-enterprise-member")
    add_enterprise_member(harness, enterprise_owner, member, summary.workspace_id)
    member = replace(member, workspace_id=summary.workspace_id)
    with pytest.raises(ToolExecutionDeniedError):
        credential_database.service.rotate(
            context(member),
            tool_id=CREDENTIAL_TOOL_ID,
            tool_version=1,
            plaintext=SECOND_SECRET,
        )


def test_encrypted_versions_audit_and_database_mutation_are_secret_free(
    credential_database: CredentialDatabase,
) -> None:
    """轮换只留下信封密文和最小审计，关联数据字段不能被数据库改写。"""

    owner = register(credential_database.harness, "p407-encryption")
    request_context = context(owner)
    first = credential_database.service.create(
        request_context,
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=FIRST_SECRET,
    )
    second = credential_database.service.rotate(
        request_context,
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=SECOND_SECRET,
    )
    assert first.credential_ref != second.credential_ref
    assert second.credential_version == 2

    with credential_database.harness.sessions() as session:
        rows = (
            session.execute(
                select(tool_credentials).where(
                    tool_credentials.c.workspace_id == owner.workspace_id
                )
            )
            .mappings()
            .all()
        )
        audits = session.execute(
            select(audit_records.c.action, audit_records.c.attributes).where(
                audit_records.c.workspace_id == owner.workspace_id,
                audit_records.c.resource_type == "tool_credential",
            )
        ).all()
        events = session.scalars(
            select(outbox_events.c.payload).where(
                outbox_events.c.workspace_id == owner.workspace_id
            )
        ).all()
        assert {row["status"] for row in rows} == {"active", "revoked"}
        serialized = json.dumps(
            {"audits": audits, "events": events},
            default=str,
            sort_keys=True,
        )
        assert FIRST_SECRET not in serialized and SECOND_SECRET not in serialized
        assert first.credential_ref not in serialized and second.credential_ref not in serialized
        assert all(FIRST_SECRET.encode() not in bytes(row["ciphertext"]) for row in rows)
        with pytest.raises(DBAPIError):
            session.execute(
                update(tool_credentials)
                .where(tool_credentials.c.credential_id == second.credential_id)
                .values(ciphertext=rows[0]["ciphertext"])
            )
            session.commit()
        session.rollback()


def test_call_binds_exact_version_once_and_injects_only_inside_callback(
    credential_database: CredentialDatabase,
) -> None:
    """ToolCall 只绑定 V1 当前引用，Adapter 回调外部只能看到脱敏结果。"""

    owner = register(credential_database.harness, "p407-call-edge")
    request_context, tasks, claim = _ready_claim(credential_database, owner, "call-edge")
    active_v1 = credential_database.service.create(
        request_context,
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=FIRST_SECRET,
    )
    active_v2 = credential_database.service.create(
        request_context,
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=2,
        plaintext=SECOND_SECRET,
    )
    binding = credential_database.service.authorize_call(claim)
    assert binding.credential_ref == active_v1.credential_ref
    assert binding.credential_version == 1
    assert credential_database.service.authorize_call(claim) == binding

    # 已授权调用不能改绑另一个工具版本，即使该引用来自同一个 Workspace。
    with credential_database.harness.sessions() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                update(tool_calls)
                .where(tool_calls.c.tool_call_id == claim.tool_call_id)
                .values(credential_ref=active_v2.credential_ref)
            )
            session.commit()
        session.rollback()

    assert tasks.transition_call(claim, "confirmed", occurred_at=datetime.now(UTC))
    assert tasks.transition_call(claim, "executing", occurred_at=datetime.now(UTC))
    seen: list[str] = []

    def adapter(secret: str) -> dict[str, object]:
        seen.append(secret)
        return {"accepted": True, "credential_length": len(secret)}

    result = credential_database.service.invoke(claim, adapter)
    assert result == {"accepted": True, "credential_length": len(FIRST_SECRET)}
    assert seen == [FIRST_SECRET]
    assert FIRST_SECRET not in json.dumps(result)


@pytest.mark.parametrize("change", ["rotate", "revoke"])
def test_rotation_and_revocation_fail_closed_after_binding(
    credential_database: CredentialDatabase,
    change: str,
) -> None:
    """调用即使已经进入 executing，活动引用变化后也不能解密或调用 Adapter。"""

    owner = register(credential_database.harness, f"p407-{change}")
    request_context, tasks, claim = _ready_claim(credential_database, owner, change)
    credential_database.service.create(
        request_context,
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=FIRST_SECRET,
    )
    _advance_to_executing(credential_database, tasks, claim)
    if change == "rotate":
        credential_database.service.rotate(
            request_context,
            tool_id=CREDENTIAL_TOOL_ID,
            tool_version=1,
            plaintext=SECOND_SECRET,
        )
    else:
        credential_database.service.revoke(
            request_context,
            tool_id=CREDENTIAL_TOOL_ID,
            tool_version=1,
        )
    calls = 0

    def adapter(_: str) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"accepted": True}

    with pytest.raises(ToolCredentialUnavailableError):
        credential_database.service.invoke(claim, adapter)
    assert calls == 0


def test_callback_result_and_exception_cannot_expose_plaintext(
    credential_database: CredentialDatabase,
) -> None:
    """Adapter 把凭证放进返回或异常时统一隔离，稳定错误本身不携带明文。"""

    owner = register(credential_database.harness, "p407-exposure")
    request_context, tasks, claim = _ready_claim(credential_database, owner, "exposure")
    credential_database.service.create(
        request_context,
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=FIRST_SECRET,
    )
    _advance_to_executing(credential_database, tasks, claim)

    with pytest.raises(ToolCredentialExposureDetectedError) as returned:
        credential_database.service.invoke(claim, lambda secret: {"debug": secret})
    assert FIRST_SECRET not in str(returned.value)

    def unsafe_exception(secret: str) -> dict[str, bool]:
        raise RuntimeError(f"synthetic adapter failed with {secret}")

    with pytest.raises(ToolCredentialExposureDetectedError) as raised:
        credential_database.service.invoke(claim, unsafe_exception)
    assert FIRST_SECRET not in str(raised.value)


def test_rotation_waits_for_inflight_call_edge_then_invalidates_old_binding(
    credential_database: CredentialDatabase,
) -> None:
    """已开始回调先完成；轮换提交后，同一旧引用的后续调用立即失败关闭。"""

    owner = register(credential_database.harness, "p407-concurrent-rotate")
    request_context, tasks, claim = _ready_claim(
        credential_database,
        owner,
        "concurrent-rotate",
    )
    credential_database.service.create(
        request_context,
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=FIRST_SECRET,
    )
    _advance_to_executing(credential_database, tasks, claim)
    adapter_started = Event()
    allow_adapter_finish = Event()
    rotation_started = Event()
    rotation_finished = Event()

    def adapter(secret: str) -> dict[str, bool]:
        assert secret == FIRST_SECRET
        adapter_started.set()
        assert allow_adapter_finish.wait(timeout=5)
        return {"accepted": True}

    def rotate() -> None:
        rotation_started.set()
        credential_database.service.rotate(
            request_context,
            tool_id=CREDENTIAL_TOOL_ID,
            tool_version=1,
            plaintext=SECOND_SECRET,
        )
        rotation_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        invocation = executor.submit(credential_database.service.invoke, claim, adapter)
        assert adapter_started.wait(timeout=5)
        rotation = executor.submit(rotate)
        assert rotation_started.wait(timeout=5)
        assert not rotation_finished.wait(timeout=0.2)
        allow_adapter_finish.set()
        assert invocation.result(timeout=5) == {"accepted": True}
        rotation.result(timeout=5)
    assert rotation_finished.is_set()
    with pytest.raises(ToolCredentialUnavailableError):
        credential_database.service.invoke(claim, lambda _: {"accepted": True})


def test_migration_refuses_to_drop_persisted_tool_credentials(
    credential_database: CredentialDatabase,
) -> None:
    """存在凭证历史时不得降级到 0056，避免静默删除密文和调用恢复线索。"""

    schema_map = credential_database.harness.engine.get_execution_options().get(
        "schema_translate_map"
    )
    assert isinstance(schema_map, dict)
    schema = schema_map["ai_platform"]
    assert isinstance(schema, str)
    migration = Config(str(ROOT / "alembic.ini"))
    migration.set_main_option("script_location", str(ROOT / "infra/migrations"))
    migration.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    migration.set_main_option(
        "sqlalchemy.url",
        credential_database.harness.engine.url.render_as_string(hide_password=False),
    )
    migration.set_main_option("ai_platform_schema", schema)
    with pytest.raises(RuntimeError, match="拒绝破坏性降级"):
        command.downgrade(migration, "20260816_0056")


def test_tool_credentials_rewrap_without_reencrypting_plaintext(
    credential_database: CredentialDatabase,
    tmp_path: Path,
) -> None:
    """主密钥轮换只更新包裹层，并保持工具凭证正文密文和关联数据有效。"""

    owner = register(credential_database.harness, "p407-rewrap")
    credential = credential_database.service.create(
        context(owner),
        tool_id=CREDENTIAL_TOOL_ID,
        tool_version=1,
        plaintext=FIRST_SECRET,
    )
    new_key_path = tmp_path / "master-v2.key"
    new_key_path.write_bytes(b"n" * 32)
    os.chmod(new_key_path, 0o600)
    with credential_database.harness.engine.begin() as connection:
        count = rewrap_tool_credentials(
            connection,
            old_key=MasterKeyFile(str(credential_database.key_path), 1),
            new_key=MasterKeyFile(str(new_key_path), 2),
        )
    assert count >= 1

    with credential_database.harness.sessions() as session:
        row = (
            session.execute(
                select(tool_credentials).where(
                    tool_credentials.c.credential_id == credential.credential_id
                )
            )
            .mappings()
            .one()
        )
    assert row["master_key_version"] == 2
    assert bytes(row["ciphertext"]) == credential.envelope.ciphertext
    rewrapped = EncryptedSecret(
        key_version=row["master_key_version"],
        encrypted_data_key=bytes(row["encrypted_data_key"]),
        data_key_nonce=bytes(row["data_key_nonce"]),
        ciphertext=bytes(row["ciphertext"]),
        data_nonce=bytes(row["data_nonce"]),
        last_four=row["last_four"],
    )
    assert (
        EnvelopeSecretCipher(MasterKeyFile(str(new_key_path), 2)).decrypt(
            rewrapped,
            associated_data=tool_credential_associated_data(
                workspace_id=credential.workspace_id,
                tool_id=credential.tool_id,
                tool_version=credential.tool_version,
                credential_id=credential.credential_id,
                credential_ref=credential.credential_ref,
                credential_version=credential.credential_version,
            ),
        )
        == FIRST_SECRET
    )
