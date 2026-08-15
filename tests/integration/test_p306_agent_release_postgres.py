"""验证 P3-06 不可变 AgentRelease、来源绑定、幂等和数据库防绕过闭环。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.agent_control.application.releases import (
    release_snapshot_digest,
    require_valid_release,
)
from ai_platform_api.modules.agent_control.application.service import (
    AgentControlService,
    AgentLifecycleConflictError,
    AgentNotFoundError,
    AgentReleaseApprovalRequiredError,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeCommand
from ai_platform_api.persistence.tables import (
    agent_approval_bindings,
    agent_control_requests,
    agent_draft_revisions,
    agent_evaluation_runs,
    agent_release_candidates,
    agent_releases,
    outbox_events,
)
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p304_agent_evaluation_postgres import (
    RegisteredAccount,
    context,
)
from tests.integration.test_p305_agent_approval_postgres import (
    ApprovalHarness,
    approval_harness,
    candidate_status,
    prepare_candidate,
    register,
)


@pytest.fixture(scope="module")
def approval_database() -> Iterator[ApprovalHarness]:
    """为 P3-06 用例注册独立的合成审批和发布数据库。"""

    yield from approval_harness()


def approve_candidate(
    harness: ApprovalHarness,
    owner: RegisteredAccount,
    suffix: str,
) -> tuple[AgentControlService, UUID]:
    """创建并批准个人空间候选，返回可进入发布门禁的合成事实。"""

    agents, candidate_id = prepare_candidate(harness, owner, suffix)
    requested = agents.request_approval(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key=f"synthetic-release-approval-{suffix}",
    )
    approved = harness.approvals.act(
        context(owner),
        approval_instance_id=requested.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            f"synthetic-release-approve-{suffix}",
        ),
    )
    assert approved.state.instance.status == "approved"
    return agents, candidate_id


def test_approved_candidate_creates_one_verifiable_release(
    approval_database: ApprovalHarness,
) -> None:
    """发布快照固定配置、评估和审批证据，相同候选始终返回唯一 Release。"""

    owner = register(approval_database, "release-owner")
    agents, candidate_id = approve_candidate(approval_database, owner, "valid")

    first = agents.publish_release(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-agent-release-valid",
    )
    replayed = agents.publish_release(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-agent-release-valid",
    )
    candidate_replayed = agents.publish_release(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-agent-release-second-key",
    )

    assert replayed == first
    assert candidate_replayed == first
    assert first.release_kind == "custom"
    assert first.version == 1
    assert first.snapshot is not None
    assert first.snapshot_hash == release_snapshot_digest(first.snapshot)
    assert first.snapshot["snapshot_schema_version"] == 1
    assert first.snapshot["source_draft_id"] == str(first.source_draft_id)
    assert first.snapshot["source_draft_revision"] == first.source_draft_revision
    evaluation_snapshot = cast("dict[str, object]", first.snapshot["evaluation"])
    approval_snapshot = cast("dict[str, object]", first.snapshot["approval"])
    assert evaluation_snapshot["status"] == "passed"
    assert approval_snapshot["status"] == "approved"
    assert candidate_status(approval_database, candidate_id) == "released"
    loaded = agents.get_release(
        context(owner),
        agent_id=first.agent_id,
        release_id=first.release_id,
    )
    assert loaded == first
    with pytest.raises(AgentLifecycleConflictError):
        require_valid_release(replace(first, snapshot_hash="f" * 64))

    with approval_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(agent_releases)
                .where(agent_releases.c.candidate_id == candidate_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(agent_control_requests)
                .where(
                    agent_control_requests.c.workspace_id == owner.workspace_id,
                    agent_control_requests.c.operation == "agent.release.publish",
                    agent_control_requests.c.result_id == first.release_id,
                )
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.workspace_id == owner.workspace_id,
                    outbox_events.c.event_type == "agent.release.published",
                    outbox_events.c.aggregate_id == first.agent_id,
                )
            )
            == 1
        )


def test_release_rejects_missing_approval_and_cross_workspace_access(
    approval_database: ApprovalHarness,
) -> None:
    """未审批候选不能发布，跨空间调用使用不存在语义隐藏候选身份。"""

    owner = register(approval_database, "release-gate-owner")
    outsider = register(approval_database, "release-gate-outsider")
    agents, candidate_id = prepare_candidate(
        approval_database,
        owner,
        "missing-approval",
    )

    with pytest.raises(AgentReleaseApprovalRequiredError):
        agents.publish_release(
            context(owner),
            candidate_id=candidate_id,
            idempotency_key="synthetic-release-without-approval",
        )
    with pytest.raises(AgentNotFoundError):
        agents.publish_release(
            context(outsider),
            candidate_id=candidate_id,
            idempotency_key="synthetic-release-cross-workspace",
        )
    with approval_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(agent_releases)
                .where(agent_releases.c.candidate_id == candidate_id)
            )
            == 0
        )


def test_database_rejects_forged_release_and_snapshot_mutation(
    approval_database: ApprovalHarness,
) -> None:
    """数据库拒绝跳过快照校验、直接推进候选或篡改已生成 Release。"""

    owner = register(approval_database, "release-database-owner")
    agents, candidate_id = approve_candidate(
        approval_database,
        owner,
        "database",
    )

    # 1. 即使外键身份齐全，空快照也不能伪装成有效自定义 Release。
    with approval_database.sessions() as session:
        candidate = session.execute(
            select(agent_release_candidates).where(
                agent_release_candidates.c.candidate_id == candidate_id
            )
        ).one()
        binding = session.execute(
            select(agent_approval_bindings).where(
                agent_approval_bindings.c.candidate_id == candidate_id
            )
        ).one()
        evaluation = session.execute(
            select(agent_evaluation_runs).where(
                agent_evaluation_runs.c.evaluation_run_id == binding.evaluation_run_id
            )
        ).one()
        revision = session.execute(
            select(agent_draft_revisions).where(
                agent_draft_revisions.c.draft_id == candidate.draft_id,
                agent_draft_revisions.c.revision == candidate.draft_revision,
            )
        ).one()
        with pytest.raises(DBAPIError):
            session.execute(
                insert(agent_releases).values(
                    release_id=uuid4(),
                    agent_id=candidate.agent_id,
                    workspace_id=candidate.workspace_id,
                    release_kind="custom",
                    version=1,
                    status="released",
                    runtime_config_version_id=UUID(
                        str(revision.configuration["runtime_config_version_id"])
                    ),
                    config_hash=candidate.config_hash,
                    candidate_id=candidate.candidate_id,
                    candidate_hash=candidate.candidate_hash,
                    source_draft_id=candidate.draft_id,
                    source_draft_revision=candidate.draft_revision,
                    evaluation_run_id=evaluation.evaluation_run_id,
                    approval_binding_id=binding.approval_binding_id,
                    snapshot={},
                    snapshot_hash="0" * 64,
                    released_by_account_id=owner.account_id,
                    released_at=datetime.now(UTC),
                )
            )
        session.rollback()

        # 2. 候选必须由同一事务内已存在的匹配 Release 推进，不能直接改终态。
        with pytest.raises(DBAPIError):
            session.execute(
                update(agent_release_candidates)
                .where(agent_release_candidates.c.candidate_id == candidate_id)
                .values(
                    status="released",
                    updated_at=datetime.now(UTC),
                    version=candidate.version + 1,
                )
            )
        session.rollback()

    release = agents.publish_release(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-agent-release-database",
    )
    with approval_database.sessions() as session:
        # 3. Release 创建后只能新增版本，任何更新或删除均由历史事实 Trigger 拒绝。
        with pytest.raises(DBAPIError):
            session.execute(
                update(agent_releases)
                .where(agent_releases.c.release_id == release.release_id)
                .values(snapshot_hash="f" * 64)
            )
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(
                delete(agent_releases).where(agent_releases.c.release_id == release.release_id)
            )
        session.rollback()
