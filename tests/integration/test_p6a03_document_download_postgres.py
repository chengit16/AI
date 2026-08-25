"""验证 P6A-03 文档详情与下载授权 Migration 的非空升级和安全回滚。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
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
    migration_database as _p403_migration_database,
)

migration_database = _p403_migration_database

DOWNLOAD_PERMISSION = "knowledge.document.download"
DOCUMENT_BINDINGS = frozenset(
    {
        (
            "82000000-0000-4000-8000-000000000147",
            "81000000-0000-4000-8000-000000000174",
            "query",
        ),
        (
            "82000000-0000-4000-8000-000000000255",
            "81000000-0000-4000-8000-000000000175",
            "query",
        ),
    }
)
MIGRATION_NOW = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)


class _MenuSnapshot(TypedDict):
    """描述 P6A-03 Migration 断言所需的菜单快照字段。"""

    registry_version: int
    menus: list[dict[str, object]]
    menu_api_bindings: list[dict[str, str]]


@dataclass(frozen=True)
class _MigrationWorkspace:
    """保存既有空间和 Registry 25 原发布身份。"""

    workspace_id: UUID
    account_id: UUID
    source_release_id: UUID
    source_snapshot: _MenuSnapshot


def test_revision_0073_upgrades_existing_workspace_and_roundtrips(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """既有 Owner、绑定与菜单快照必须同步升级，并能精确恢复。"""

    config, connection, schema, _ = migration_database
    workspace = _seed_pre_0073_workspace(migration_database)

    command.upgrade(config, "head")
    connection.commit()
    assert _revision(connection, schema) == "20260825_0073"
    assert _owner_download_permission_count(connection, schema, workspace.workspace_id) == 1
    assert _registered_document_bindings(connection, schema) == DOCUMENT_BINDINGS
    upgraded_release_id, upgraded_snapshot = _current_menu_snapshot(
        connection,
        schema,
        workspace.workspace_id,
    )
    assert upgraded_release_id != workspace.source_release_id
    assert upgraded_snapshot["registry_version"] == 26
    assert {str(item["menu_id"]) for item in upgraded_snapshot["menus"]} >= {
        "82000000-0000-4000-8000-000000000145",
        "82000000-0000-4000-8000-000000000255",
    }
    snapshot_bindings = {
        (str(item["menu_id"]), str(item["api_resource_id"]), str(item["action_type"]))
        for item in upgraded_snapshot["menu_api_bindings"]
    }
    assert snapshot_bindings >= DOCUMENT_BINDINGS

    command.downgrade(config, "20260823_0072")
    connection.commit()
    assert _owner_download_permission_count(connection, schema, workspace.workspace_id) == 0
    assert _registered_document_bindings(connection, schema) == frozenset()
    restored_release_id, restored_snapshot = _current_menu_snapshot(
        connection,
        schema,
        workspace.workspace_id,
    )
    assert restored_release_id == workspace.source_release_id
    assert restored_snapshot == workspace.source_snapshot
    assert (
        connection.scalar(
            text(f'SELECT count(*) FROM "{schema}".menu_releases WHERE release_id = :release_id'),
            {"release_id": upgraded_release_id},
        )
        == 0
    )

    command.upgrade(config, "head")
    connection.commit()
    assert _revision(connection, schema) == "20260825_0073"
    assert _owner_download_permission_count(connection, schema, workspace.workspace_id) == 1


def test_revision_0073_blocks_destructive_downgrade(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """自定义授权或后续菜单发布存在时不得静默回滚。"""

    config, connection, schema, _ = migration_database
    workspace = _seed_pre_0073_workspace(migration_database)
    command.upgrade(config, "head")
    connection.commit()

    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, :permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            WHERE roles.workspace_id = :workspace_id AND roles.role_key = 'workspace_member'
            """
        ),
        {"workspace_id": workspace.workspace_id, "permission_code": DOWNLOAD_PERMISSION},
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="存在自定义文档下载授权"):
        command.downgrade(config, "20260823_0072")
    connection.rollback()
    assert _revision(connection, schema) == "20260825_0073"

    connection.execute(
        text(
            f'DELETE FROM "{schema}".role_permission_grants '
            "WHERE workspace_id = :workspace_id AND permission_code = :permission_code "
            "AND role_id IN (SELECT role_id FROM "
            f"\"{schema}\".roles WHERE role_key = 'workspace_member')"
        ),
        {"workspace_id": workspace.workspace_id, "permission_code": DOWNLOAD_PERMISSION},
    )
    connection.commit()
    _append_followup_menu_release(connection, schema, workspace)
    with pytest.raises(RuntimeError, match="当前菜单发布已在 P6A-03 后变化"):
        command.downgrade(config, "20260823_0072")
    connection.rollback()
    assert _revision(connection, schema) == "20260825_0073"


def _seed_pre_0073_workspace(
    migration_database: tuple[Config, Connection, str, str],
) -> _MigrationWorkspace:
    """在 0072 建立既有 Owner 和非空 Registry 25 菜单发布。"""

    config, _, schema, database_url = migration_database
    command.upgrade(config, "20260823_0072")
    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    try:
        registration = RegistrationService(
            SqlAlchemyIdentityReader(sessions),
            SqlAlchemyRegistrationUnitOfWork(sessions),
            Argon2idPasswordAdapter(),
        ).register(
            login_name=f"synthetic.p6a03.migration.{uuid4().hex}@example.com",
            display_name="合成 P6A-03 迁移用户",
            password="synthetic-password-123",
            request_id=uuid4(),
            trace=TraceContext("7" * 32, "8" * 16),
        )
        source_release_id = uuid4()
        source_snapshot: _MenuSnapshot = {
            "registry_version": 25,
            "menus": [
                {
                    "menu_id": "82000000-0000-4000-8000-000000000145",
                    "menu_key": "navigation.workspace.knowledge",
                }
            ],
            "menu_api_bindings": [],
        }
        with sessions.begin() as session:
            # 当前应用代码已包含下载权限，删除后才能还原真实的 0072 历史状态。
            session.execute(
                delete(role_permission_grants).where(
                    role_permission_grants.c.workspace_id == registration.personal_workspace_id,
                    role_permission_grants.c.permission_code == DOWNLOAD_PERMISSION,
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
                    snapshot=source_snapshot,
                    snapshot_digest=_snapshot_digest(source_snapshot),
                    validation_errors=[],
                    rejection_reason=None,
                    created_by_account_id=registration.account_id,
                    decided_by_account_id=registration.account_id,
                    created_at=MIGRATION_NOW,
                    validated_at=MIGRATION_NOW,
                    decided_at=MIGRATION_NOW,
                    published_at=MIGRATION_NOW,
                    version=1,
                )
            )
            session.execute(
                insert(workspace_menu_publications).values(
                    workspace_id=registration.personal_workspace_id,
                    current_release_id=source_release_id,
                    published_at=MIGRATION_NOW,
                )
            )
        return _MigrationWorkspace(
            workspace_id=registration.personal_workspace_id,
            account_id=registration.account_id,
            source_release_id=source_release_id,
            source_snapshot=source_snapshot,
        )
    finally:
        engine.dispose()


def _append_followup_menu_release(
    connection: Connection,
    schema: str,
    workspace: _MigrationWorkspace,
) -> None:
    """追加 P6A-03 后发布，验证降级不会猜测回退目标。"""

    current_release_id, snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )
    followup_release_id = uuid4()
    release_number = int(
        connection.scalar(
            text(
                f'SELECT COALESCE(MAX(release_number), 0) + 1 FROM "{schema}".menu_releases '
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace.workspace_id},
        )
        or 1
    )
    connection.execute(
        text(
            f'INSERT INTO "{schema}".menu_releases ('
            "release_id, workspace_id, release_number, release_kind, source_release_id, "
            "status, snapshot, snapshot_digest, validation_errors, rejection_reason, "
            "created_by_account_id, decided_by_account_id, created_at, validated_at, "
            "decided_at, published_at, version) VALUES ("
            ":release_id, :workspace_id, :release_number, 'standard', :source_release_id, "
            "'published', CAST(:snapshot AS jsonb), :snapshot_digest, ARRAY[]::varchar[], "
            "NULL, :account_id, :account_id, :occurred_at, :occurred_at, :occurred_at, "
            ":occurred_at, 8)"
        ),
        {
            "release_id": followup_release_id,
            "workspace_id": workspace.workspace_id,
            "release_number": release_number,
            "source_release_id": current_release_id,
            "snapshot": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            "snapshot_digest": _snapshot_digest(snapshot),
            "account_id": workspace.account_id,
            "occurred_at": MIGRATION_NOW,
        },
    )
    connection.execute(
        text(
            f'UPDATE "{schema}".workspace_menu_publications '
            "SET current_release_id = :release_id, published_at = :published_at "
            "WHERE workspace_id = :workspace_id"
        ),
        {
            "release_id": followup_release_id,
            "published_at": MIGRATION_NOW,
            "workspace_id": workspace.workspace_id,
        },
    )
    connection.commit()


def _revision(connection: Connection, schema: str) -> str:
    return cast(
        str,
        connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')),
    )


def _owner_download_permission_count(
    connection: Connection,
    schema: str,
    workspace_id: UUID,
) -> int:
    return int(
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".role_permission_grants AS grants '
                f'JOIN "{schema}".roles AS roles '
                "ON roles.workspace_id = grants.workspace_id AND roles.role_id = grants.role_id "
                "WHERE grants.workspace_id = :workspace_id "
                "AND roles.role_key = 'workspace_owner' "
                "AND grants.permission_code = :permission_code"
            ),
            {"workspace_id": workspace_id, "permission_code": DOWNLOAD_PERMISSION},
        )
        or 0
    )


def _registered_document_bindings(
    connection: Connection,
    schema: str,
) -> frozenset[tuple[str, str, str]]:
    rows = connection.execute(
        text(
            f'SELECT menu_id, api_resource_id, action_type FROM "{schema}".'
            "registered_menu_api_bindings WHERE api_resource_id = ANY(:api_resource_ids)"
        ),
        {"api_resource_ids": [UUID(api_id) for _, api_id, _ in DOCUMENT_BINDINGS]},
    )
    return frozenset(
        (str(row.menu_id), str(row.api_resource_id), str(row.action_type)) for row in rows
    )


def _current_menu_snapshot(
    connection: Connection,
    schema: str,
    workspace_id: UUID,
) -> tuple[UUID, _MenuSnapshot]:
    row = connection.execute(
        text(
            f"SELECT releases.release_id, releases.snapshot "
            f'FROM "{schema}".workspace_menu_publications AS publications '
            f'JOIN "{schema}".menu_releases AS releases '
            "ON releases.workspace_id = publications.workspace_id "
            "AND releases.release_id = publications.current_release_id "
            "WHERE publications.workspace_id = :workspace_id"
        ),
        {"workspace_id": workspace_id},
    ).one()
    return cast(UUID, row.release_id), cast(_MenuSnapshot, row.snapshot)


def _snapshot_digest(snapshot: _MenuSnapshot) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
