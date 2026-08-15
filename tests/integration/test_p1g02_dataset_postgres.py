"""验证 P1G-01 固定数据集可装入当前 Schema 并保持隔离与版本边界。"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    accounts,
    departments,
    document_publications,
    document_sources,
    document_versions,
    documents,
    knowledge_bases,
    membership_departments,
    role_bindings,
    roles,
    workspace_memberships,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, and_, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.support.p1g01_loader import (
    LoadedP1G01Dataset,
    load_p1g01_dataset,
    validate_p1g01_dataset,
)

ROOT = Path(__file__).parents[2]
FIXTURE_PATH = ROOT / "tests/fixtures/e2e/p1g01-v1.json"
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
LOCAL_PASSWORD = "synthetic-password-123"


@dataclass(frozen=True)
class P1G02DatasetHarness:
    """持有已装载固定数据集的隔离数据库入口和非敏感摘要。"""

    engine: Engine
    sessions: sessionmaker[Session]
    dataset: dict[str, Any]
    summary: LoadedP1G01Dataset


def load_fixture() -> dict[str, Any]:
    """读取固定对象型 Fixture，拒绝错误顶层结构。"""

    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("P1G-01 Fixture 顶层必须是对象")
    return cast(dict[str, Any], document)


@pytest.fixture(scope="module")
def p1g02_dataset() -> Iterator[P1G02DatasetHarness]:
    """从空 Schema 迁移到 head，并在单一事务中装入完整固定数据图。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1g02_test_{uuid4().hex}"
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
    dataset = load_fixture()
    with sessions.begin() as session:
        summary = load_p1g01_dataset(session, dataset, local_password=LOCAL_PASSWORD)
    try:
        yield P1G02DatasetHarness(engine, sessions, dataset, summary)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_full_fixed_dataset_loads_into_current_head_schema(
    p1g02_dataset: P1G02DatasetHarness,
) -> None:
    summary = p1g02_dataset.summary
    assert summary.account_count == 100
    assert summary.enterprise_membership_count == 100
    assert summary.department_count == 7
    assert summary.document_count == 364
    assert summary.document_version_count == 388
    assert summary.publication_count == 324

    with p1g02_dataset.sessions() as session:
        assert session.scalar(select(func.count()).select_from(accounts)) == 100
        assert session.scalar(select(func.count()).select_from(workspace_memberships)) == 101
        assert session.scalar(select(func.count()).select_from(departments)) == 7
        assert session.scalar(select(func.count()).select_from(membership_departments)) == 104
        assert session.scalar(select(func.count()).select_from(knowledge_bases)) == 8
        assert session.scalar(select(func.count()).select_from(documents)) == 364
        assert session.scalar(select(func.count()).select_from(document_versions)) == 388
        assert session.scalar(select(func.count()).select_from(document_sources)) == 388


def test_loaded_identity_roles_and_passwords_keep_expected_boundaries(
    p1g02_dataset: P1G02DatasetHarness,
) -> None:
    with p1g02_dataset.sessions() as session:
        owner = session.execute(
            select(accounts).where(accounts.c.login_name == "synthetic.p1g01.user001@example.com")
        ).one()
        disabled_members = session.scalar(
            select(func.count())
            .select_from(workspace_memberships)
            .where(
                workspace_memberships.c.workspace_id
                == p1g02_dataset.summary.enterprise_workspace_id,
                workspace_memberships.c.status == "disabled",
            )
        )
        role_count = session.scalar(select(func.count()).select_from(roles))
        binding_count = session.scalar(select(func.count()).select_from(role_bindings))

    assert owner.account_id == p1g02_dataset.summary.owner_account_id
    assert Argon2idPasswordAdapter().verify(owner.password_hash, LOCAL_PASSWORD) is True
    assert LOCAL_PASSWORD not in owner.password_hash
    assert disabled_members == 3
    assert role_count == 11
    assert binding_count == 110


def test_loaded_documents_never_publish_deleted_or_nonpublished_versions(
    p1g02_dataset: P1G02DatasetHarness,
) -> None:
    with p1g02_dataset.sessions() as session:
        publication_count = session.scalar(select(func.count()).select_from(document_publications))
        invalid_publications = session.scalar(
            select(func.count())
            .select_from(
                document_publications.join(
                    documents,
                    and_(
                        documents.c.workspace_id == document_publications.c.workspace_id,
                        documents.c.document_id == document_publications.c.document_id,
                    ),
                ).join(
                    document_versions,
                    and_(
                        document_versions.c.workspace_id == document_publications.c.workspace_id,
                        document_versions.c.document_id == document_publications.c.document_id,
                        document_versions.c.document_version_id
                        == document_publications.c.current_document_version_id,
                    ),
                )
            )
            .where((documents.c.status != "active") | (document_versions.c.status != "published"))
        )
        workspace_document_counts: dict[UUID, int] = {
            cast(UUID, row.workspace_id): cast(int, row.count)
            for row in session.execute(
                select(
                    documents.c.workspace_id,
                    func.count().label("count"),
                ).group_by(documents.c.workspace_id)
            )
        }

    assert publication_count == p1g02_dataset.summary.publication_count
    assert invalid_publications == 0
    assert workspace_document_counts == {
        p1g02_dataset.summary.enterprise_workspace_id: 360,
        p1g02_dataset.summary.personal_workspace_id: 4,
    }


def test_loader_rejects_wrong_version_non_synthetic_data_and_digest_drift() -> None:
    source = load_fixture()
    wrong_version = deepcopy(source)
    wrong_version["dataset_version"] = "p1g01-v2"
    non_synthetic = deepcopy(source)
    non_synthetic["users"][0]["synthetic"] = False
    digest_drift = deepcopy(source)
    digest_drift["metrics"]["user_count"] = 99

    for invalid in (wrong_version, non_synthetic, digest_drift):
        with pytest.raises(ValueError):
            validate_p1g01_dataset(invalid)
