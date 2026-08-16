"""建立 P4-07 工具凭证版本和调用边缘引用绑定。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0057"
down_revision: str | None = "20260816_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建加密凭证事实，并只允许 ToolCall 在授权边一次性绑定活动引用。"""

    schema = _schema()
    _create_tool_credentials(schema)
    _create_credential_functions(schema)
    _create_credential_triggers(schema)
    op.create_foreign_key(
        "fk_tool_calls_credential_binding",
        "tool_calls",
        "tool_credentials",
        ["credential_ref", "workspace_id", "tool_id", "tool_version"],
        ["credential_ref", "workspace_id", "tool_id", "tool_version"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.execute(sa.text(_credential_call_transition_sql(schema)))


def downgrade() -> None:
    """只在从未保存或绑定工具凭证时恢复 P4-06 结构。"""

    schema = _schema()
    credential_count = op.get_bind().scalar(
        sa.text(f'SELECT count(*) FROM "{schema}".tool_credentials')
    )
    bound_call_count = op.get_bind().scalar(
        sa.text(f'SELECT count(*) FROM "{schema}".tool_calls WHERE credential_ref IS NOT NULL')
    )
    if int(credential_count or 0) > 0 or int(bound_call_count or 0) > 0:
        raise RuntimeError("存在工具凭证或已绑定调用, 拒绝破坏性降级")

    op.execute(sa.text(_legacy_call_transition_sql(schema)))
    op.drop_constraint(
        "fk_tool_calls_credential_binding",
        "tool_calls",
        schema=schema,
        type_="foreignkey",
    )
    _drop_credential_triggers(schema)
    _drop_credential_functions(schema)
    op.drop_index(
        "ix_tool_credentials_workspace_time",
        table_name="tool_credentials",
        schema=schema,
    )
    op.drop_index(
        "uq_tool_credentials_active",
        table_name="tool_credentials",
        schema=schema,
    )
    op.drop_table("tool_credentials", schema=schema)


def _create_tool_credentials(schema: str) -> None:
    op.create_table(
        "tool_credentials",
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("credential_ref", sa.String(69), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("master_key_version", sa.Integer(), nullable=False),
        sa.Column("encrypted_data_key", sa.LargeBinary(), nullable=False),
        sa.Column("data_key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("data_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("last_four", sa.String(4), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("credential_ref", name="uq_tool_credentials_ref"),
        sa.UniqueConstraint(
            "workspace_id",
            "tool_id",
            "tool_version",
            "credential_version",
            name="uq_tool_credentials_version",
        ),
        sa.UniqueConstraint(
            "credential_ref",
            "workspace_id",
            "tool_id",
            "tool_version",
            name="uq_tool_credentials_binding",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_tool_credentials_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id", "tool_version"],
            [
                f"{schema}.agent_tool_definitions.tool_id",
                f"{schema}.agent_tool_definitions.tool_version",
            ],
            name="fk_tool_credentials_definition",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_tool_credentials_creator",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_tool_credentials_revoker",
        ),
        sa.CheckConstraint(
            "credential_ref ~ '^cred_[a-z0-9]{16,64}$'",
            name="ck_tool_credentials_ref",
        ),
        sa.CheckConstraint(
            "tool_version >= 1 AND credential_version >= 1 AND master_key_version >= 1",
            name="ck_tool_credentials_versions",
        ),
        sa.CheckConstraint(
            "octet_length(encrypted_data_key) >= 32 "
            "AND octet_length(data_key_nonce) = 12 "
            "AND octet_length(ciphertext) BETWEEN 24 AND 4112 "
            "AND octet_length(data_nonce) = 12",
            name="ck_tool_credentials_envelope",
        ),
        sa.CheckConstraint(
            "char_length(last_four) = 4",
            name="ck_tool_credentials_last_four",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_tool_credentials_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND revoked_by_account_id IS NULL AND revoked_at IS NULL) "
            "OR (status = 'revoked' AND revoked_by_account_id IS NOT NULL "
            "AND revoked_at IS NOT NULL AND revoked_at >= created_at)",
            name="ck_tool_credentials_revocation",
        ),
        schema=schema,
    )
    op.create_index(
        "uq_tool_credentials_active",
        "tool_credentials",
        ["workspace_id", "tool_id", "tool_version"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_tool_credentials_workspace_time",
        "tool_credentials",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_credential_functions(schema: str) -> None:
    op.execute(sa.text(_credential_insert_sql(schema)))
    op.execute(sa.text(_credential_mutation_sql(schema)))


def _create_credential_triggers(schema: str) -> None:
    statements = (
        (
            f'CREATE TRIGGER trg_tool_credentials_validate BEFORE INSERT ON "{schema}".'
            "tool_credentials FOR EACH ROW EXECUTE FUNCTION "
            f'"{schema}".validate_tool_credential_insert()'
        ),
        (
            f'CREATE TRIGGER trg_tool_credentials_mutation BEFORE UPDATE OR DELETE ON "{schema}".'
            "tool_credentials FOR EACH ROW EXECUTE FUNCTION "
            f'"{schema}".validate_tool_credential_mutation()'
        ),
    )
    for statement in statements:
        op.execute(sa.text(statement))


def _drop_credential_triggers(schema: str) -> None:
    op.execute(
        sa.text(f'DROP TRIGGER trg_tool_credentials_mutation ON "{schema}".tool_credentials')
    )
    op.execute(
        sa.text(f'DROP TRIGGER trg_tool_credentials_validate ON "{schema}".tool_credentials')
    )


def _drop_credential_functions(schema: str) -> None:
    op.execute(sa.text(f'DROP FUNCTION "{schema}".validate_tool_credential_mutation()'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".validate_tool_credential_insert()'))


def _credential_insert_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_credential_insert() RETURNS trigger AS $$
    DECLARE expected_version integer;
    BEGIN
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".agent_tool_definitions definition
          WHERE definition.tool_id = NEW.tool_id
            AND definition.tool_version = NEW.tool_version
            AND definition.status = 'active'
            AND definition.access_mode = 'write'
            AND definition.adapter_kind = 'synthetic_internal_write'
            AND definition.credential_requirement = 'credential_ref'
            AND definition.synthetic = true) THEN
        RAISE EXCEPTION 'tool credential definition is not manageable';
      END IF;
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".workspaces workspace
          JOIN "{schema}".workspace_memberships membership
            ON membership.workspace_id = workspace.workspace_id
          WHERE workspace.workspace_id = NEW.workspace_id
            AND workspace.status = 'active'
            AND membership.account_id = NEW.created_by_account_id
            AND membership.membership_type = 'owner'
            AND membership.status = 'active') THEN
        RAISE EXCEPTION 'tool credential creator is not active workspace owner';
      END IF;
      SELECT COALESCE(max(credential_version), 0) + 1 INTO expected_version
        FROM "{schema}".tool_credentials
       WHERE workspace_id = NEW.workspace_id AND tool_id = NEW.tool_id
         AND tool_version = NEW.tool_version;
      IF NEW.credential_version <> expected_version THEN
        RAISE EXCEPTION 'tool credential version must be contiguous';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _credential_mutation_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_credential_mutation() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'tool credential history is immutable';
      END IF;
      IF ROW(NEW.credential_id, NEW.credential_ref, NEW.workspace_id, NEW.tool_id,
             NEW.tool_version, NEW.credential_version, NEW.ciphertext, NEW.data_nonce,
             NEW.last_four, NEW.created_by_account_id, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.credential_id, OLD.credential_ref, OLD.workspace_id, OLD.tool_id,
             OLD.tool_version, OLD.credential_version, OLD.ciphertext, OLD.data_nonce,
             OLD.last_four, OLD.created_by_account_id, OLD.created_at) THEN
        RAISE EXCEPTION 'tool credential authenticated identity is immutable';
      END IF;

      -- 主密钥轮换只重包裹数据密钥, 不能与业务状态变化合并为一次更新。
      IF ROW(NEW.master_key_version, NEW.encrypted_data_key, NEW.data_key_nonce)
         IS DISTINCT FROM
         ROW(OLD.master_key_version, OLD.encrypted_data_key, OLD.data_key_nonce) THEN
        IF NEW.master_key_version <= OLD.master_key_version
           OR ROW(NEW.status, NEW.revoked_by_account_id, NEW.revoked_at)
              IS DISTINCT FROM
              ROW(OLD.status, OLD.revoked_by_account_id, OLD.revoked_at) THEN
          RAISE EXCEPTION 'illegal tool credential data key rewrap';
        END IF;
        RETURN NEW;
      END IF;

      IF OLD.status <> 'active' OR NEW.status <> 'revoked'
         OR NEW.revoked_by_account_id IS NULL OR NEW.revoked_at IS NULL
         OR NOT EXISTS (
            SELECT 1 FROM "{schema}".workspace_memberships membership
             WHERE membership.workspace_id = OLD.workspace_id
               AND membership.account_id = NEW.revoked_by_account_id
               AND membership.membership_type = 'owner'
               AND membership.status = 'active') THEN
        RAISE EXCEPTION 'illegal tool credential status transition';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _credential_call_transition_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_call_transition()
    RETURNS trigger AS $$
    DECLARE requirement varchar(32);
    BEGIN
      IF ROW(NEW.run_id, NEW.step_id, NEW.attempt_id, NEW.workspace_id, NEW.tool_id,
             NEW.tool_version, NEW.canonical_arguments_hash, NEW.access_mode,
             NEW.risk_level, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.step_id, OLD.attempt_id, OLD.workspace_id, OLD.tool_id,
             OLD.tool_version, OLD.canonical_arguments_hash, OLD.access_mode,
             OLD.risk_level, OLD.created_at) THEN
        RAISE EXCEPTION 'tool call binding is immutable';
      END IF;
      SELECT credential_requirement INTO requirement
        FROM "{schema}".agent_tool_definitions
       WHERE tool_id = NEW.tool_id AND tool_version = NEW.tool_version;
      IF requirement IS NULL THEN
        RAISE EXCEPTION 'tool call definition is unavailable';
      END IF;

      IF OLD.state = 'proposed' AND NEW.state = 'authorized' THEN
        IF requirement = 'credential_ref' THEN
          IF OLD.credential_ref IS NOT NULL OR NEW.credential_ref IS NULL
             OR NOT EXISTS (
                SELECT 1 FROM "{schema}".tool_credentials credential
                 WHERE credential.credential_ref = NEW.credential_ref
                   AND credential.workspace_id = NEW.workspace_id
                   AND credential.tool_id = NEW.tool_id
                   AND credential.tool_version = NEW.tool_version
                   AND credential.status = 'active') THEN
            RAISE EXCEPTION 'tool call credential binding is unavailable';
          END IF;
        ELSIF OLD.credential_ref IS NOT NULL OR NEW.credential_ref IS NOT NULL THEN
          RAISE EXCEPTION 'credential-free tool call cannot bind credential';
        END IF;
      ELSIF NEW.credential_ref IS DISTINCT FROM OLD.credential_ref THEN
        RAISE EXCEPTION 'tool call credential binding is immutable';
      END IF;

      IF requirement = 'credential_ref'
         AND NEW.state IN ('authorized', 'confirmed', 'executing')
         AND NOT EXISTS (
            SELECT 1 FROM "{schema}".tool_credentials credential
             WHERE credential.credential_ref = NEW.credential_ref
               AND credential.workspace_id = NEW.workspace_id
               AND credential.tool_id = NEW.tool_id
               AND credential.tool_version = NEW.tool_version
               AND credential.status = 'active') THEN
        RAISE EXCEPTION 'tool call credential was revoked or rotated';
      END IF;
      IF OLD.state IN ('succeeded', 'failed', 'cancelled', 'timed_out') THEN
        RAISE EXCEPTION 'tool call terminal state is immutable';
      END IF;
      IF NOT ((OLD.state = 'proposed' AND NEW.state IN ('authorized', 'cancelled', 'timed_out'))
          OR (OLD.state = 'authorized' AND NEW.state IN ('confirmed', 'cancelled', 'timed_out'))
          OR (OLD.state = 'confirmed' AND NEW.state IN ('executing', 'cancelled', 'timed_out'))
          OR (OLD.state = 'executing' AND NEW.state IN
              ('succeeded', 'failed', 'cancelled', 'timed_out'))) THEN
        RAISE EXCEPTION 'illegal tool call transition: % -> %', OLD.state, NEW.state;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _legacy_call_transition_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_call_transition()
    RETURNS trigger AS $$
    BEGIN
      IF ROW(NEW.run_id, NEW.step_id, NEW.attempt_id, NEW.workspace_id, NEW.tool_id,
             NEW.tool_version, NEW.canonical_arguments_hash, NEW.access_mode,
             NEW.risk_level, NEW.credential_ref, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.step_id, OLD.attempt_id, OLD.workspace_id, OLD.tool_id,
             OLD.tool_version, OLD.canonical_arguments_hash, OLD.access_mode,
             OLD.risk_level, OLD.credential_ref, OLD.created_at) THEN
        RAISE EXCEPTION 'tool call binding is immutable';
      END IF;
      IF OLD.state IN ('succeeded', 'failed', 'cancelled', 'timed_out') THEN
        RAISE EXCEPTION 'tool call terminal state is immutable';
      END IF;
      IF NOT ((OLD.state = 'proposed' AND NEW.state IN ('authorized', 'cancelled', 'timed_out'))
          OR (OLD.state = 'authorized' AND NEW.state IN ('confirmed', 'cancelled', 'timed_out'))
          OR (OLD.state = 'confirmed' AND NEW.state IN ('executing', 'cancelled', 'timed_out'))
          OR (OLD.state = 'executing' AND NEW.state IN
              ('succeeded', 'failed', 'cancelled', 'timed_out'))) THEN
        RAISE EXCEPTION 'illegal tool call transition: % -> %', OLD.state, NEW.state;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """
