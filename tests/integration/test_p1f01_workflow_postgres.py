"""验证 P1F-01 工作流版本、运行事实和 HTTP 安全边界的 PostgreSQL 闭环。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.workflow.api.routes import router as workflow_router
from ai_platform_api.modules.workflow.application.service import (
    WorkflowConflictError,
    WorkflowDefinitionService,
    WorkflowIdempotencyConflictError,
    WorkflowNotFoundError,
)
from ai_platform_api.modules.workflow.domain.models import (
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)
from ai_platform_api.modules.workflow.infrastructure.sqlalchemy import (
    SqlAlchemyWorkflowUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import audit_records, outbox_events, workflow_versions
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("c" * 32, "d" * 16)


@dataclass(frozen=True)
class RegisteredAccount:
    """保留测试创建的账号与默认个人空间标识。"""

    account_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class WorkflowHarness:
    """集中持有临时 Schema 的账号注册、工作流服务和数据库入口。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    workflows: WorkflowDefinitionService


@pytest.fixture(scope="module")
def workflow_database() -> Iterator[WorkflowHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1f01_test_{uuid4().hex}"
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
    try:
        yield WorkflowHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            WorkflowDefinitionService(SqlAlchemyWorkflowUnitOfWork(sessions)),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: WorkflowHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.workflow.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成工作流用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def context(account: RegisteredAccount, *, workspace_id: UUID | None = None) -> RequestContext:
    base = RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id or account.workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )
    return replace(base, authorized_workspace=True)


def graph(result_name: str = "合成结果") -> WorkflowGraph:
    return WorkflowGraph(
        schema_version=1,
        entry_node_id="trigger",
        nodes=(
            WorkflowNode("trigger", "trigger", "合成触发器", {}),
            WorkflowNode("result", "result", result_name, {}),
        ),
        edges=(WorkflowEdge("trigger_to_result", "trigger", "result"),),
    )


def test_publication_run_freeze_idempotency_and_immutability(
    workflow_database: WorkflowHarness,
) -> None:
    owner = register(workflow_database, "version-owner")
    owner_context = context(owner)
    workflow, draft = workflow_database.workflows.create(
        owner_context,
        name="  合成审批流  ",
        description="仅用于 P1F-01 测试",
        graph=graph(),
    )
    _, first_version, first_publication = workflow_database.workflows.publish(
        owner_context,
        workflow_id=workflow.workflow_id,
        expected_revision=draft.revision,
    )
    first_run = workflow_database.workflows.create_run(
        owner_context,
        workflow_id=workflow.workflow_id,
        workflow_version_id=first_version.workflow_version_id,
        idempotency_key="synthetic-workflow-run-0001",
        input_payload={"request": "合成审批请求"},
    )
    repeated = workflow_database.workflows.create_run(
        owner_context,
        workflow_id=workflow.workflow_id,
        workflow_version_id=first_version.workflow_version_id,
        idempotency_key="synthetic-workflow-run-0001",
        input_payload={"request": "合成审批请求"},
    )
    assert repeated == first_run
    assert first_run.status == "queued"
    assert first_run.workflow_version_id == first_publication.workflow_version_id

    with pytest.raises(WorkflowIdempotencyConflictError):
        workflow_database.workflows.create_run(
            owner_context,
            workflow_id=workflow.workflow_id,
            workflow_version_id=first_version.workflow_version_id,
            idempotency_key="synthetic-workflow-run-0001",
            input_payload={"request": "不同的合成审批请求"},
        )

    updated = workflow_database.workflows.update_draft(
        owner_context,
        workflow_id=workflow.workflow_id,
        expected_revision=draft.revision,
        graph=graph("合成结果 V2"),
    )
    _, second_version, publication = workflow_database.workflows.publish(
        owner_context,
        workflow_id=workflow.workflow_id,
        expected_revision=updated.revision,
    )
    assert second_version.version_number == 2
    assert publication.generation == 2
    assert (
        workflow_database.workflows.get_run(
            replace(
                owner_context,
                authorized_workspace=False,
                authorized_resource_ids=frozenset({first_run.workflow_run_id}),
            ),
            workflow_id=workflow.workflow_id,
            workflow_run_id=first_run.workflow_run_id,
        ).workflow_version_id
        == first_version.workflow_version_id
    )

    # 当前发布指针推进后，旧版本仍可追溯，但不能再创建新的运行。
    with pytest.raises(WorkflowConflictError):
        workflow_database.workflows.create_run(
            owner_context,
            workflow_id=workflow.workflow_id,
            workflow_version_id=first_version.workflow_version_id,
            idempotency_key="synthetic-workflow-run-0002",
            input_payload={},
        )
    with pytest.raises(DBAPIError), workflow_database.sessions.begin() as session:
        session.execute(
            update(workflow_versions)
            .where(workflow_versions.c.workflow_version_id == first_version.workflow_version_id)
            .values(graph_digest="f" * 64)
        )

    with workflow_database.sessions() as session:
        assert (session.scalar(select(func.count()).select_from(audit_records)) or 0) >= 5
        assert (session.scalar(select(func.count()).select_from(outbox_events)) or 0) >= 5


def test_cross_workspace_and_http_run_projection(workflow_database: WorkflowHarness) -> None:
    owner = register(workflow_database, "http-owner")
    outsider = register(workflow_database, "http-outsider")
    owner_context = context(owner)
    workflow, draft = workflow_database.workflows.create(
        owner_context,
        name="合成 HTTP 工作流",
        description=None,
        graph=graph(),
    )
    _, version, _ = workflow_database.workflows.publish(
        owner_context,
        workflow_id=workflow.workflow_id,
        expected_revision=draft.revision,
    )
    run = workflow_database.workflows.create_run(
        owner_context,
        workflow_id=workflow.workflow_id,
        workflow_version_id=version.workflow_version_id,
        idempotency_key="synthetic-workflow-http-0001",
        input_payload={"secret": "仅限合成字段级授权测试"},
    )

    with pytest.raises(WorkflowNotFoundError):
        workflow_database.workflows.get(
            context(outsider, workspace_id=outsider.workspace_id),
            workflow_id=workflow.workflow_id,
        )

    masked_context = replace(
        owner_context,
        authorized_workspace=False,
        authorized_resource_ids=frozenset({run.workflow_run_id}),
        authorized_field_mask=frozenset({"input"}),
    )
    app = FastAPI()
    app.state.workflow_definition_service = workflow_database.workflows
    app.state.field_projection_service = FieldProjectionService(
        load_field_policy_registry(ROOT / "contracts/authorization/field-policy-registry.v1.json")
    )
    app.include_router(workflow_router, prefix="/api/v1")
    app.dependency_overrides[trusted_request_context] = lambda: masked_context
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/workspaces/{owner.workspace_id}/workflows/{workflow.workflow_id}"
            f"/runs/{run.workflow_run_id}"
        )
        list_response = client.get(
            f"/api/v1/workspaces/{owner.workspace_id}/workflows/{workflow.workflow_id}/runs"
        )

    assert response.status_code == 200
    assert response.json()["workflow_run_id"] == str(run.workflow_run_id)
    assert response.json()["input_payload"] is None
    assert list_response.status_code == 200
    assert [item["workflow_run_id"] for item in list_response.json()["items"]] == [
        str(run.workflow_run_id)
    ]
    assert list_response.json()["items"][0]["input_payload"] is None
