"""建立 P1D-01 知识库、文档、版本、来源与发布事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0016"
down_revision: str | None = "20260814_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KNOWLEDGE_PERMISSIONS = (
    "knowledge.base.create",
    "knowledge.base.delete",
    "knowledge.document.create",
    "knowledge.document.delete",
    "knowledge.document.version.create",
    "knowledge.document.version.publish",
    "knowledge.document.version.ready",
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_knowledge_bases(schema)
    _create_documents(schema)
    _create_document_versions(schema)
    _create_document_sources(schema)
    _create_document_publications(schema)

    permissions = ", ".join(f"('{code}')" for code in KNOWLEDGE_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[], 'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS permission_codes(permission_code)
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )
    bindings = ", ".join(
        f"('82000000-0000-4000-8000-{menu:012d}'::uuid, "
        f"'81000000-0000-4000-8000-{api:012d}'::uuid, '{action}')"
        for menu, api, action in (
            (138, 45, "mutation"),
            (139, 46, "mutation"),
            (140, 47, "mutation"),
            (141, 48, "mutation"),
            (142, 49, "mutation"),
            (143, 50, "mutation"),
            (144, 51, "publish"),
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
                menu_id, api_resource_id, action_type
            ) VALUES {bindings}
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE menu_id BETWEEN
                  '82000000-0000-4000-8000-000000000138'::uuid AND
                  '82000000-0000-4000-8000-000000000144'::uuid
            """
        )
    )
    permissions = ", ".join(f"'{code}'" for code in KNOWLEDGE_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND roles.role_key = 'workspace_owner'
              AND roles.system_managed = true
              AND grants.permission_code IN ({permissions})
            """
        )
    )
    op.drop_table("document_publications", schema=schema)
    op.drop_table("document_sources", schema=schema)
    op.drop_index(
        "ix_document_versions_workspace_document_status",
        table_name="document_versions",
        schema=schema,
    )
    op.drop_table("document_versions", schema=schema)
    op.drop_index(
        "ix_documents_workspace_base_status",
        table_name="documents",
        schema=schema,
    )
    op.drop_table("documents", schema=schema)
    op.drop_index(
        "uq_knowledge_bases_active_name",
        table_name="knowledge_bases",
        schema=schema,
    )
    op.drop_table("knowledge_bases", schema=schema)


def _create_knowledge_bases(schema: str) -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("default_visibility", sa.String(32), nullable=False),
        sa.Column(
            "department_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("default_security_level", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            name="uq_knowledge_bases_workspace_base",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_knowledge_bases_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_knowledge_bases_creator",
        ),
        sa.CheckConstraint(
            "default_visibility IN ('private', 'workspace', 'departments')",
            name="ck_knowledge_bases_visibility",
        ),
        sa.CheckConstraint(
            "(default_visibility = 'departments' AND cardinality(department_ids) > 0) "
            "OR (default_visibility <> 'departments' AND cardinality(department_ids) = 0)",
            name="ck_knowledge_bases_department_scope",
        ),
        sa.CheckConstraint(
            "default_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_knowledge_bases_security_level",
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_knowledge_bases_status"),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) "
            "OR (status = 'active' AND deleted_at IS NULL)",
            name="ck_knowledge_bases_deleted_at",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_knowledge_bases_name",
        ),
        sa.CheckConstraint("version >= 1", name="ck_knowledge_bases_version"),
        schema=schema,
    )
    op.create_index(
        "uq_knowledge_bases_active_name",
        "knowledge_bases",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        schema=schema,
    )


def _create_documents(schema: str) -> None:
    op.create_table(
        "documents",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column(
            "department_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("security_level", sa.String(32), nullable=False),
        sa.Column(
            "permission_labels",
            postgresql.ARRAY(sa.String(80)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("workspace_id", "document_id", name="uq_documents_workspace_document"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "knowledge_base_id"],
            [
                f"{schema}.knowledge_bases.workspace_id",
                f"{schema}.knowledge_bases.knowledge_base_id",
            ],
            name="fk_documents_knowledge_base",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_documents_creator",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'workspace', 'departments')",
            name="ck_documents_visibility",
        ),
        sa.CheckConstraint(
            "(visibility = 'departments' AND cardinality(department_ids) > 0) "
            "OR (visibility <> 'departments' AND cardinality(department_ids) = 0)",
            name="ck_documents_department_scope",
        ),
        sa.CheckConstraint(
            "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_documents_security_level",
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_documents_status"),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) "
            "OR (status = 'active' AND deleted_at IS NULL)",
            name="ck_documents_deleted_at",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 255",
            name="ck_documents_title",
        ),
        sa.CheckConstraint("version >= 1", name="ck_documents_version"),
        schema=schema,
    )
    op.create_index(
        "ix_documents_workspace_base_status",
        "documents",
        ["workspace_id", "knowledge_base_id", "status"],
        schema=schema,
    )


def _create_document_versions(schema: str) -> None:
    op.create_table(
        "document_versions",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "document_id", "version_number", name="uq_document_versions_number"
        ),
        sa.UniqueConstraint(
            "workspace_id", "document_version_id", name="uq_document_versions_workspace_version"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "document_id",
            "document_version_id",
            name="uq_document_versions_document_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_document_versions_document",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_document_versions_creator",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'ready', 'published', 'superseded')",
            name="ck_document_versions_status",
        ),
        sa.CheckConstraint(
            "(status IN ('published', 'superseded') AND published_at IS NOT NULL) "
            "OR (status IN ('draft', 'ready') AND published_at IS NULL)",
            name="ck_document_versions_published_at",
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND content_hash IS NULL) "
            "OR (status <> 'draft' AND content_hash ~ '^[0-9a-f]{64}$')",
            name="ck_document_versions_content_hash",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_document_versions_number"),
        sa.CheckConstraint("record_version >= 1", name="ck_document_versions_record_version"),
        schema=schema,
    )
    op.create_index(
        "ix_document_versions_workspace_document_status",
        "document_versions",
        ["workspace_id", "document_id", "status"],
        schema=schema,
    )


def _create_document_sources(schema: str) -> None:
    op.create_table(
        "document_sources",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("original_object_key", sa.String(1024), nullable=True),
        sa.Column("source_path", sa.String(2048), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("external_source_id", sa.String(512), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "document_version_id", name="uq_document_sources_version"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            [
                f"{schema}.document_versions.workspace_id",
                f"{schema}.document_versions.document_version_id",
            ],
            name="fk_document_sources_version",
        ),
        sa.CheckConstraint(
            "source_kind IN ('manual', 'upload', 'web', 'data_source')",
            name="ck_document_sources_kind",
        ),
        sa.CheckConstraint(
            "(source_kind = 'manual' AND original_object_key IS NULL AND source_path IS NULL "
            "AND source_url IS NULL AND external_source_id IS NULL) OR "
            "(source_kind = 'upload' AND original_object_key IS NOT NULL "
            "AND source_url IS NULL) OR "
            "(source_kind = 'web' AND source_url IS NOT NULL AND original_object_key IS NULL) OR "
            "(source_kind = 'data_source' AND external_source_id IS NOT NULL)",
            name="ck_document_sources_locator",
        ),
        sa.CheckConstraint(
            "char_length(btrim(source_name)) BETWEEN 1 AND 255",
            name="ck_document_sources_name",
        ),
        schema=schema,
    )


def _create_document_publications(schema: str) -> None:
    op.create_table(
        "document_publications",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "current_document_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_document_publications_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id", "current_document_version_id"],
            [
                f"{schema}.document_versions.workspace_id",
                f"{schema}.document_versions.document_id",
                f"{schema}.document_versions.document_version_id",
            ],
            name="fk_document_publications_version",
        ),
        schema=schema,
    )
