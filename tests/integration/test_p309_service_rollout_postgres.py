"""验证 P3-09 灰度、晋级、回滚、在途绑定和并发发布闭环。"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID

import pytest
from ai_platform_api.modules.assistant.application.service import (
    AssistantConversationService,
)
from ai_platform_api.modules.assistant.infrastructure.sqlalchemy import (
    SqlAlchemyAssistantUnitOfWork,
)
from ai_platform_api.modules.service_governance.application.service import (
    ServiceGovernanceService,
    ServiceRouteConflictError,
)
from ai_platform_api.modules.service_governance.domain.models import ServiceDeployment
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceGovernanceUnitOfWork,
    SqlAlchemyServiceRepository,
)
from ai_platform_api.modules.service_runtime.infrastructure.sqlalchemy import (
    SqlAlchemyRuntimeSnapshotSource,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    outbox_events,
    service_control_requests,
    service_routes,
)
from sqlalchemy import func, select

from tests.integration.test_p1e01_assistant_postgres import (
    AssistantHarness,
    context,
    publish_runtime_config,
    register,
)
from tests.integration.test_p308_runtime_isolation_postgres import runtime_harness


class RecordingInvalidator:
    """记录事务提交后的 current 缓存失效，不访问真实 Valkey。"""

    def __init__(self) -> None:
        self.service_ids: list[tuple[UUID, UUID]] = []

    def invalidate_current(self, workspace_id: UUID, service_id: UUID) -> None:
        self.service_ids.append((workspace_id, service_id))


def governance(
    harness: AssistantHarness,
    invalidator: RecordingInvalidator | None = None,
) -> ServiceGovernanceService:
    return ServiceGovernanceService(
        SqlAlchemyServiceGovernanceUnitOfWork(harness.sessions),
        invalidator,
    )


@pytest.fixture(scope="module")
def rollout_database() -> Iterator[AssistantHarness]:
    """为 P3-09 用例创建独立 Schema，避免改变 P3-08 的历史验收数据。"""

    yield from runtime_harness()


def test_canary_promote_rollback_preserve_stable_selection_and_inflight_binding(
    rollout_database: AssistantHarness,
) -> None:
    """Route 只追加，新 Run 观察切换，在途 Run 仍按灰度 Route 的冻结 Release 完成。"""

    # 长函数保留原因: 同一条发布链必须连续证明灰度选择、Trigger、晋级、回滚和在途绑定。
    # 1. 生成同一系统 Agent 的两个不可变 Release，第二版成为当前稳定主版本。
    runtime_database = rollout_database
    owner = register(runtime_database, "rollout-owner")
    owner_context = context(owner)
    publish_runtime_config(runtime_database, owner.account_id, version=6)
    first_conversation = runtime_database.assistant.create_conversation(
        owner_context,
        title="合成灰度第一版会话",
    )
    first_run = runtime_database.assistant.create_user_message(
        owner_context,
        conversation_id=first_conversation.conversation_id,
        texts=("合成第一版请求",),
        idempotency_key="synthetic-p309-first-run",
    ).run
    assert runtime_database.assistant.claim_run(owner_context, run_id=first_run.run_id) is not None
    runtime_database.assistant.complete_run(
        owner_context,
        run_id=first_run.run_id,
        text="合成第一版完成",
    )

    publish_runtime_config(runtime_database, owner.account_id, version=7)
    second_conversation = runtime_database.assistant.create_conversation(
        owner_context,
        title="合成灰度第二版会话",
    )
    service = governance(runtime_database)
    management_context = replace(owner_context, authorized_workspace=True)
    stable = service.list_services(management_context)[0]
    first_release_id = first_run.agent_release_id
    second_release_id = stable.route.primary_release_id
    assert stable.publication.generation == 2
    assert first_release_id != second_release_id

    # 2. 灰度 Route 对固定百分位稳定选择，助手新 Run 的 primary 绑定由新 Trigger 接受。
    invalidator = RecordingInvalidator()
    service = governance(runtime_database, invalidator)
    canary = service.start_canary(
        management_context,
        service_id=stable.service.service_id,
        release_id=first_release_id,
        canary_percent=10,
        expected_generation=2,
        idempotency_key="synthetic-p309-canary",
    )
    source = SqlAlchemyRuntimeSnapshotSource(runtime_database.sessions)
    canary_selected = source.get_current(owner.workspace_id, stable.service.service_id, 0)
    primary_selected = source.get_current(owner.workspace_id, stable.service.service_id, 99)
    repeated = source.get_current(owner.workspace_id, stable.service.service_id, 0)
    assert canary.route.route_mode == "canary"
    assert canary_selected is not None and primary_selected is not None
    assert repeated == canary_selected
    assert canary_selected.agent_release_id == first_release_id
    assert primary_selected.agent_release_id == second_release_id

    inflight = runtime_database.assistant.create_user_message(
        owner_context,
        conversation_id=second_conversation.conversation_id,
        texts=("合成灰度在途请求",),
        idempotency_key="synthetic-p309-inflight-run",
    ).run
    assert inflight.service_route_id == canary.route.route_id
    assert inflight.agent_release_id == second_release_id
    assert runtime_database.assistant.claim_run(owner_context, run_id=inflight.run_id) is not None

    # 3. 晋级和回滚各追加一条 Route；历史精确读取及 Release 摘要保持不变。
    with runtime_database.sessions() as session:
        original_hashes = dict(
            session.execute(
                select(agent_releases.c.release_id, agent_releases.c.snapshot_hash).where(
                    agent_releases.c.release_id.in_((first_release_id, second_release_id))
                )
            )
            .tuples()
            .all()
        )
    promoted = service.promote_route(
        management_context,
        service_id=stable.service.service_id,
        release_id=first_release_id,
        expected_generation=3,
        idempotency_key="synthetic-p309-promote",
    )
    rolled_back = service.rollback_route(
        management_context,
        service_id=stable.service.service_id,
        expected_generation=4,
        idempotency_key="synthetic-p309-rollback",
    )
    historical = source.get_bound(
        owner.workspace_id,
        stable.service.service_id,
        canary.route.route_id,
        canary.route.route_version,
        second_release_id,
    )
    assert promoted.route.route_mode == "active"
    assert promoted.route.primary_release_id == first_release_id
    assert rolled_back.route.route_mode == "rollback"
    assert rolled_back.route.primary_release_id == second_release_id
    assert rolled_back.route.previous_route_id == promoted.route.route_id
    assert historical is not None and historical.agent_release_id == second_release_id
    completed = runtime_database.assistant.complete_run(
        owner_context,
        run_id=inflight.run_id,
        text="合成灰度在途完成",
    )
    assert completed.status == "completed"
    assert len(invalidator.service_ids) == 3

    with runtime_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(service_routes)
                .where(service_routes.c.service_id == stable.service.service_id)
            )
            == 5
        )
        assert (
            dict(
                session.execute(
                    select(agent_releases.c.release_id, agent_releases.c.snapshot_hash).where(
                        agent_releases.c.release_id.in_((first_release_id, second_release_id))
                    )
                )
                .tuples()
                .all()
            )
            == original_hashes
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(service_control_requests)
                .where(
                    service_control_requests.c.service_id == stable.service.service_id,
                    service_control_requests.c.operation.like("service.route.%"),
                )
            )
            == 3
        )


def test_system_route_sync_invalidates_current_only_after_route_change(
    rollout_database: AssistantHarness,
) -> None:
    """系统助手只在首建或 Release 切换提交后失效 current 缓存。"""

    runtime_database = rollout_database
    owner = register(runtime_database, "rollout-system-sync-owner")
    owner_context = context(owner)
    invalidator = RecordingInvalidator()
    assistant = AssistantConversationService(
        SqlAlchemyAssistantUnitOfWork(
            runtime_database.sessions,
            SqlAlchemyServiceRepository,
        ),
        current_route_invalidator=invalidator,
    )

    publish_runtime_config(runtime_database, owner.account_id, version=1)
    assistant.create_conversation(owner_context, title="合成系统路由首建")
    assistant.create_conversation(owner_context, title="合成系统路由无变化重放")
    publish_runtime_config(runtime_database, owner.account_id, version=2)
    assistant.create_conversation(owner_context, title="合成系统路由切换")

    assert len(invalidator.service_ids) == 2
    assert invalidator.service_ids[0] == invalidator.service_ids[1]


def test_same_generation_concurrent_promotions_have_one_winner(
    rollout_database: AssistantHarness,
) -> None:
    """两个不同 Release 的并发晋级只有一个胜者，失败事务不留下 Route 或事件副作用。"""

    runtime_database = rollout_database
    owner = register(runtime_database, "rollout-concurrency-owner")
    owner_context = context(owner)
    management_context = replace(owner_context, authorized_workspace=True)
    release_ids = []
    service_id = None
    for offset in range(3):
        publish_runtime_config(runtime_database, owner.account_id, version=3 + offset)
        runtime_database.assistant.create_conversation(
            owner_context,
            title=f"合成并发发布版本 {offset + 1}",
        )
        current = governance(runtime_database).list_services(management_context)[0]
        service_id = current.service.service_id
        release_ids.append(current.route.primary_release_id)
    assert service_id is not None
    current = governance(runtime_database).get_service(
        management_context,
        service_id=service_id,
    )
    expected_generation = current.publication.generation

    with runtime_database.sessions() as session:
        routes_before = session.scalar(
            select(func.count())
            .select_from(service_routes)
            .where(service_routes.c.service_id == service_id)
        )
        events_before = session.scalar(
            select(func.count())
            .select_from(outbox_events)
            .where(
                outbox_events.c.aggregate_id == service_id,
                outbox_events.c.event_type == "service.route.promoted",
            )
        )

    def publish(index: int) -> ServiceDeployment:
        return governance(runtime_database).promote_route(
            management_context,
            service_id=service_id,
            release_id=release_ids[index],
            expected_generation=expected_generation,
            idempotency_key=f"synthetic-p309-concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish, index) for index in (0, 1)]
    successes = [future.result() for future in futures if future.exception() is None]
    failures = [future.exception() for future in futures if future.exception() is not None]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ServiceRouteConflictError)
    winner = successes[0]
    assert winner.route.primary_release_id in set(release_ids[:2])

    with runtime_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(service_routes)
                .where(service_routes.c.service_id == service_id)
            )
            == (routes_before or 0) + 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.aggregate_id == service_id,
                    outbox_events.c.event_type == "service.route.promoted",
                )
            )
            == (events_before or 0) + 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(service_control_requests)
                .where(
                    service_control_requests.c.service_id == service_id,
                    service_control_requests.c.operation == "service.route.promote",
                    service_control_requests.c.idempotency_key.like("synthetic-p309-concurrent-%"),
                )
            )
            == 1
        )
