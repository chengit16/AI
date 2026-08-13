"""强化 P1B-01 默认个人空间与唯一所有者约束。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260814_0006"
down_revision: str | None = "20260813_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    # 历史数据需先满足规范化登录名和空间所有者规则，不能静默改写身份事实。
    invalid_login = op.get_bind().scalar(
        sa.text(
            f'SELECT EXISTS (SELECT 1 FROM "{schema}".accounts '
            "WHERE login_name <> lower(btrim(login_name)) OR char_length(login_name) < 3)"
        )
    )
    invalid_workspace = op.get_bind().scalar(
        sa.text(
            f'SELECT EXISTS (SELECT 1 FROM "{schema}".workspaces '
            "WHERE (workspace_type = 'personal' AND owner_account_id IS NULL) "
            "OR (workspace_type = 'enterprise' AND owner_account_id IS NOT NULL))"
        )
    )
    if invalid_login:
        raise RuntimeError("现有账号不满足规范化登录名约束")
    if invalid_workspace:
        raise RuntimeError("现有工作空间不满足个人/企业所有者约束")
    op.create_check_constraint(
        "ck_accounts_normalized_login",
        "accounts",
        "login_name = lower(btrim(login_name)) AND char_length(login_name) >= 3",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_workspaces_owner_by_type",
        "workspaces",
        "(workspace_type = 'personal' AND owner_account_id IS NOT NULL) "
        "OR (workspace_type = 'enterprise' AND owner_account_id IS NULL)",
        schema=schema,
    )
    op.create_index(
        "uq_workspaces_personal_owner",
        "workspaces",
        ["owner_account_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("workspace_type = 'personal'"),
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "uq_workspaces_personal_owner",
        table_name="workspaces",
        schema=schema,
    )
    op.drop_constraint(
        "ck_workspaces_owner_by_type",
        "workspaces",
        type_="check",
        schema=schema,
    )
    op.drop_constraint(
        "ck_accounts_normalized_login",
        "accounts",
        type_="check",
        schema=schema,
    )
