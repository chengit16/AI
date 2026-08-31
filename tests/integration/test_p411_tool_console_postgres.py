"""验证 P4-11 工具控制台投影、跨空间隔离和 Migration 往返。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import TypedDict, cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.tool_execution.application.console import ToolConsoleService
from ai_platform_api.modules.tool_execution.application.errors import ToolExecutionDeniedError
from ai_platform_api.modules.tool_execution.application.results import ToolProgressService
from ai_platform_api.modules.tool_execution.infrastructure.console_sqlalchemy import (
    SqlAlchemyToolConsoleStore,
)
from ai_platform_api.modules.tool_execution.infrastructure.results_sqlalchemy import (
    SqlAlchemyToolProgressStore,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    menu_releases,
    role_permission_grants,
    workspace_menu_publications,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, delete, insert, text

from tests.integration.test_p403_tool_task_state_postgres import (
    NOW,
    _database,
    _ready_run,
)
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _p403_migration_database,
)

migration_database = _p403_migration_database
TOOL_PERMISSIONS = frozenset(
    {
        "tool.page.access",
        "tool.catalog.read",
        "tool.run.create",
        "tool.run.read",
        "tool.run.cancel",
        "tool.confirmation.respond",
    }
)


class _MenuSnapshotItem(TypedDict):
    """描述迁移断言实际读取的菜单快照最小字段。"""

    menu_key: str


class _MenuSnapshot(TypedDict):
    """描述迁移断言需要的 Registry 版本与菜单集合。"""

    registry_version: int
    menus: list[_MenuSnapshotItem]


def test_console_projection_is_minimal_resource_scoped_and_cross_workspace_safe(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """真实查询仅返回脱敏摘要，并服从工作空间或精确 Run 资源范围。"""

    database = _database(migration_database)
    try:
        run_id, _ = _ready_run(database, "p411-console-0001")
        console = ToolConsoleService(SqlAlchemyToolConsoleStore(database.sessions))
        workspace_context = replace(
            database.context,
            authorized_permission_code="tool.run.read",
            authorized_workspace=True,
        )
        summaries = console.list_runs(workspace_context, limit=20)
        detail = console.get_run(workspace_context, run_id=run_id)
        assert [item.run_id for item in summaries] == [run_id]
        assert detail.run.service_name == "合成 P4-03 Service"
        assert detail.steps[0].tool_key == "knowledge.search"
        assert detail.latest_cursor > 0

        serialized = json.dumps(asdict(detail), default=str, ensure_ascii=False, sort_keys=True)
        assert all(
            f'"{forbidden}":' not in serialized
            for forbidden in (
                "arguments",
                "result_payload",
                "credential",
                "worker_id",
                "trace_id",
                "traceparent",
            )
        )

        resource_context = replace(
            workspace_context,
            authorized_workspace=False,
            authorized_resource_ids=frozenset({run_id}),
        )
        assert console.get_run(resource_context, run_id=run_id).run.run_id == run_id
        assert console.list_runs(resource_context, limit=20)[0].run_id == run_id
        with pytest.raises(ToolExecutionDeniedError):
            console.get_run(
                replace(resource_context, authorized_resource_ids=frozenset()),
                run_id=run_id,
            )
        with pytest.raises(ToolExecutionDeniedError):
            console.get_run(
                replace(workspace_context, workspace_id=uuid4()),
                run_id=run_id,
            )

        progress = ToolProgressService(SqlAlchemyToolProgressStore(database.sessions))
        page = progress.replay(workspace_context, run_id=run_id)
        assert [event.cursor for event in page.events] == list(range(1, page.latest_cursor + 1))
        assert all(event.workspace_id == database.context.workspace_id for event in page.events)
    finally:
        database.engine.dispose()


def test_revision_0061_grants_existing_owner_and_restores_menu_snapshot_on_roundtrip(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """真实验证数组参数绑定、Owner 回填和 Registry 21/22 当前菜单指针恢复。"""

    config, connection, schema, database_url = migration_database
    command.upgrade(config, "20260816_0060")
    connection.commit()
    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    registration = RegistrationService(
        SqlAlchemyIdentityReader(sessions),
        SqlAlchemyRegistrationUnitOfWork(sessions),
        Argon2idPasswordAdapter(),
    ).register(
        login_name=f"synthetic.p411.{uuid4().hex}@example.com",
        display_name="合成 P4-11 迁移用户",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TraceContext("3" * 32, "4" * 16),
    )
    source_release_id = uuid4()
    snapshot = {"registry_version": 21, "menus": [], "menu_api_bindings": []}
    digest = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with sessions.begin() as session:
        # 当前代码已支持新空间权限；先移除以精确模拟 0060 时代已存在的 Owner。
        session.execute(
            delete(role_permission_grants).where(
                role_permission_grants.c.workspace_id == registration.personal_workspace_id,
                role_permission_grants.c.permission_code.in_(TOOL_PERMISSIONS),
            )
        )
        session.execute(
            insert(menu_releases).values(
                release_id=source_release_id,
                workspace_id=registration.personal_workspace_id,
                release_number=1,
                release_kind="standard",
                source_release_id=None,
                status="published",
                snapshot=snapshot,
                snapshot_digest=digest,
                validation_errors=[],
                rejection_reason=None,
                created_by_account_id=registration.account_id,
                decided_by_account_id=registration.account_id,
                created_at=NOW,
                validated_at=NOW,
                decided_at=NOW,
                published_at=NOW,
                version=1,
            )
        )
        session.execute(
            insert(workspace_menu_publications).values(
                workspace_id=registration.personal_workspace_id,
                current_release_id=source_release_id,
                published_at=NOW,
            )
        )
    engine.dispose()

    command.upgrade(config, "20260816_0061")
    connection.commit()
    assert _owner_tool_permission_count(connection, schema, registration.personal_workspace_id) == 6
    upgraded_release_id, upgraded_snapshot = _current_snapshot(
        connection,
        schema,
        registration.personal_workspace_id,
    )
    assert upgraded_release_id != source_release_id
    assert upgraded_snapshot["registry_version"] == 22
    assert {item["menu_key"] for item in upgraded_snapshot["menus"]} >= {
        "workspace.tools",
        "workspace.tool_runs",
    }

    # 降级必须通过 `ANY(:permission_codes)` 数组绑定，并精确恢复原发布指针与快照。
    command.downgrade(config, "20260816_0060")
    connection.commit()
    assert _owner_tool_permission_count(connection, schema, registration.personal_workspace_id) == 0
    restored_release_id, restored_snapshot = _current_snapshot(
        connection,
        schema,
        registration.personal_workspace_id,
    )
    assert restored_release_id == source_release_id
    assert restored_snapshot == snapshot
    assert (
        connection.scalar(
            text(f'SELECT count(*) FROM "{schema}".menu_releases WHERE release_id = :release_id'),
            {"release_id": upgraded_release_id},
        )
        == 0
    )

    command.upgrade(config, "head")
    connection.commit()
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260831_0076"
    )
    assert _owner_tool_permission_count(connection, schema, registration.personal_workspace_id) == 6


def _owner_tool_permission_count(connection: Connection, schema: str, workspace_id: UUID) -> int:
    return int(
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".role_permission_grants '
                "WHERE workspace_id = :workspace_id AND permission_code LIKE 'tool.%'"
            ),
            {"workspace_id": workspace_id},
        )
        or 0
    )


def _current_snapshot(
    connection: Connection,
    schema: str,
    workspace_id: UUID,
) -> tuple[UUID, _MenuSnapshot]:
    row = connection.execute(
        text(
            f"SELECT releases.release_id, releases.snapshot "
            f'FROM "{schema}".workspace_menu_publications '
            f'AS publications JOIN "{schema}".menu_releases AS releases '
            "ON releases.workspace_id = publications.workspace_id "
            "AND releases.release_id = publications.current_release_id "
            "WHERE publications.workspace_id = :workspace_id"
        ),
        {"workspace_id": workspace_id},
    ).one()
    return cast(UUID, row.release_id), cast(_MenuSnapshot, row.snapshot)
