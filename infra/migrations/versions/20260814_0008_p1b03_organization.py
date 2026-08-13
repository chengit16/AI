"""建立 P1B-03 多级部门、岗位与成员组织归属事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0008"
down_revision: str | None = "20260814_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.create_unique_constraint(
        "uq_workspace_memberships_workspace_membership",
        "workspace_memberships",
        ["workspace_id", "membership_id"],
        schema=schema,
    )
    op.create_table(
        "departments",
        sa.Column("department_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "department_id",
            name="uq_departments_workspace_department",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_departments_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_department_id"],
            [f"{schema}.departments.workspace_id", f"{schema}.departments.department_id"],
            name="fk_departments_parent",
        ),
        sa.CheckConstraint(
            "parent_department_id <> department_id",
            name="ck_departments_not_self_parent",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_departments_status",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_departments_name",
        ),
        sa.CheckConstraint("version >= 1", name="ck_departments_version"),
        schema=schema,
    )
    op.create_index(
        "uq_departments_sibling_name",
        "departments",
        ["workspace_id", "parent_department_id", sa.text("lower(name)")],
        unique=True,
        schema=schema,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_departments_workspace_parent",
        "departments",
        ["workspace_id", "parent_department_id"],
        schema=schema,
    )
    op.create_table(
        "department_closure",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ancestor_department_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("descendant_department_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ancestor_department_id"],
            [f"{schema}.departments.workspace_id", f"{schema}.departments.department_id"],
            name="fk_department_closure_ancestor",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "descendant_department_id"],
            [f"{schema}.departments.workspace_id", f"{schema}.departments.department_id"],
            name="fk_department_closure_descendant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("depth >= 0", name="ck_department_closure_depth"),
        schema=schema,
    )
    op.create_index(
        "ix_department_closure_descendant",
        "department_closure",
        ["workspace_id", "descendant_department_id", "depth"],
        schema=schema,
    )
    op.create_table(
        "positions",
        sa.Column("position_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "department_id",
            "position_id",
            name="uq_positions_workspace_department_position",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "department_id"],
            [f"{schema}.departments.workspace_id", f"{schema}.departments.department_id"],
            name="fk_positions_department",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_positions_status",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_positions_name",
        ),
        sa.CheckConstraint("version >= 1", name="ck_positions_version"),
        schema=schema,
    )
    op.create_index(
        "ix_positions_workspace_department",
        "positions",
        ["workspace_id", "department_id"],
        schema=schema,
    )
    op.create_index(
        "uq_positions_department_name",
        "positions",
        ["workspace_id", "department_id", sa.text("lower(name)")],
        unique=True,
        schema=schema,
    )
    op.create_table(
        "membership_departments",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "membership_id"],
            [
                f"{schema}.workspace_memberships.workspace_id",
                f"{schema}.workspace_memberships.membership_id",
            ],
            name="fk_membership_departments_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "department_id"],
            [f"{schema}.departments.workspace_id", f"{schema}.departments.department_id"],
            name="fk_membership_departments_department",
        ),
        schema=schema,
    )
    op.create_index(
        "uq_membership_departments_primary",
        "membership_departments",
        ["workspace_id", "membership_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_index(
        "ix_membership_departments_scope",
        "membership_departments",
        ["workspace_id", "department_id", "membership_id"],
        schema=schema,
    )
    op.create_table(
        "membership_positions",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "membership_id"],
            [
                f"{schema}.workspace_memberships.workspace_id",
                f"{schema}.workspace_memberships.membership_id",
            ],
            name="fk_membership_positions_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "membership_id", "department_id"],
            [
                f"{schema}.membership_departments.workspace_id",
                f"{schema}.membership_departments.membership_id",
                f"{schema}.membership_departments.department_id",
            ],
            name="fk_membership_positions_department_assignment",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "department_id", "position_id"],
            [
                f"{schema}.positions.workspace_id",
                f"{schema}.positions.department_id",
                f"{schema}.positions.position_id",
            ],
            name="fk_membership_positions_position",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_membership_positions_scope",
        "membership_positions",
        ["workspace_id", "department_id", "position_id", "membership_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_membership_positions_scope",
        table_name="membership_positions",
        schema=schema,
    )
    op.drop_table("membership_positions", schema=schema)
    op.drop_index(
        "ix_membership_departments_scope",
        table_name="membership_departments",
        schema=schema,
    )
    op.drop_index(
        "uq_membership_departments_primary",
        table_name="membership_departments",
        schema=schema,
    )
    op.drop_table("membership_departments", schema=schema)
    op.drop_index(
        "uq_positions_department_name",
        table_name="positions",
        schema=schema,
    )
    op.drop_index(
        "ix_positions_workspace_department",
        table_name="positions",
        schema=schema,
    )
    op.drop_table("positions", schema=schema)
    op.drop_index(
        "ix_department_closure_descendant",
        table_name="department_closure",
        schema=schema,
    )
    op.drop_table("department_closure", schema=schema)
    op.drop_index(
        "ix_departments_workspace_parent",
        table_name="departments",
        schema=schema,
    )
    op.drop_index(
        "uq_departments_sibling_name",
        table_name="departments",
        schema=schema,
    )
    op.drop_table("departments", schema=schema)
    op.drop_constraint(
        "uq_workspace_memberships_workspace_membership",
        "workspace_memberships",
        type_="unique",
        schema=schema,
    )
