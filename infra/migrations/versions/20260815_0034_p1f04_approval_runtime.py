"""建立 P1F-04 审批实例、层级、指派和不可变动作事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0034"
down_revision: str | None = "20260815_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_PERMISSIONS = (
    "approval.instance.approve",
    "approval.instance.create",
    "approval.instance.process_due",
    "approval.instance.read",
    "approval.instance.reject",
    "approval.instance.transfer",
    "approval.instance.withdraw",
)
MEMBER_PERMISSIONS = (
    "approval.instance.approve",
    "approval.instance.create",
    "approval.instance.read",
    "approval.instance.reject",
    "approval.instance.transfer",
    "approval.instance.withdraw",
)
APPROVAL_RUNTIME_BINDINGS = (
    (182, 93, "mutation"),
    (183, 94, "query"),
    (188, 95, "mutation"),
    (183, 96, "query"),
    (184, 97, "mutation"),
    (185, 98, "mutation"),
    (186, 99, "mutation"),
    (187, 100, "mutation"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """按聚合依赖顺序建表，再安装不可变保护和授权事实。"""

    schema = _schema()
    _create_instances(schema)
    _create_levels(schema)
    _create_assignments(schema)
    _create_actions(schema)
    _protect_actions(schema)
    _backfill_permissions(schema)
    _register_bindings(schema)


def downgrade() -> None:
    """先移除授权与触发器，再按外键逆序删除审批运行表。"""

    schema = _schema()
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in APPROVAL_RUNTIME_BINDINGS
    )
    permissions = ", ".join(
        f"'{code}'" for code in sorted(set(OWNER_PERMISSIONS) | set(MEMBER_PERMISSIONS))
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id IN ({api_ids});
            DELETE FROM "{schema}".role_permission_grants
            WHERE permission_code IN ({permissions});
            DROP TRIGGER IF EXISTS protect_approval_actions
            ON "{schema}".approval_actions;
            DROP FUNCTION IF EXISTS "{schema}".reject_approval_action_mutation();
            """
        )
    )
    op.drop_index(
        "ix_approval_actions_instance_time",
        table_name="approval_actions",
        schema=schema,
    )
    op.drop_table("approval_actions", schema=schema)
    op.drop_index(
        "ix_approval_assignments_account_status",
        table_name="approval_assignments",
        schema=schema,
    )
    op.drop_table("approval_assignments", schema=schema)
    op.drop_index(
        "ix_approval_instance_levels_due",
        table_name="approval_instance_levels",
        schema=schema,
    )
    op.drop_table("approval_instance_levels", schema=schema)
    op.drop_index(
        "ix_approval_instances_requester_status",
        table_name="approval_instances",
        schema=schema,
    )
    op.drop_index(
        "ix_approval_instances_workspace_time",
        table_name="approval_instances",
        schema=schema,
    )
    op.drop_table("approval_instances", schema=schema)


def _create_instances(schema: str) -> None:
    op.create_table(
        "approval_instances",
        sa.Column("approval_instance_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_policy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requester_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("chain_digest", sa.String(64), nullable=False),
        sa.Column("personal_owner_confirmation", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_sequence_no", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "approval_instance_id",
            "workspace_id",
            name="uq_approval_instances_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "requester_account_id",
            "idempotency_key",
            name="uq_approval_instances_request_idempotency",
        ),
        sa.UniqueConstraint("workflow_step_id", name="uq_approval_instances_workflow_step"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_approval_instances_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requester_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_approval_instances_requester",
        ),
        sa.ForeignKeyConstraint(
            ["approval_policy_id", "workspace_id", "approval_policy_version_id"],
            [
                f"{schema}.approval_policy_versions.approval_policy_id",
                f"{schema}.approval_policy_versions.workspace_id",
                f"{schema}.approval_policy_versions.approval_policy_version_id",
            ],
            name="fk_approval_instances_policy_version",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id", "workspace_id"],
            [f"{schema}.workflow_runs.workflow_run_id", f"{schema}.workflow_runs.workspace_id"],
            name="fk_approval_instances_workflow_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_step_id", "workflow_run_id", "workspace_id"],
            [
                f"{schema}.workflow_run_steps.workflow_step_id",
                f"{schema}.workflow_run_steps.workflow_run_id",
                f"{schema}.workflow_run_steps.workspace_id",
            ],
            name="fk_approval_instances_workflow_step",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name="ck_approval_instances_status",
        ),
        sa.CheckConstraint(
            "current_sequence_no BETWEEN 1 AND 5",
            name="ck_approval_instances_sequence",
        ),
        sa.CheckConstraint("version >= 1", name="ck_approval_instances_version"),
        sa.CheckConstraint(
            "subject_digest ~ '^[0-9a-f]{64}$' AND chain_digest ~ '^[0-9a-f]{64}$' "
            "AND request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_approval_instances_digests",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_approval_instances_idempotency",
        ),
        sa.CheckConstraint(
            "(approval_policy_id IS NULL AND approval_policy_version_id IS NULL) OR "
            "(approval_policy_id IS NOT NULL AND approval_policy_version_id IS NOT NULL)",
            name="ck_approval_instances_policy_pair",
        ),
        sa.CheckConstraint(
            "(workflow_run_id IS NULL AND workflow_step_id IS NULL) OR "
            "(workflow_run_id IS NOT NULL AND workflow_step_id IS NOT NULL)",
            name="ck_approval_instances_workflow_pair",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status IN ('approved', 'rejected', 'withdrawn') AND completed_at IS NOT NULL)",
            name="ck_approval_instances_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_approval_instances_workspace_time",
        "approval_instances",
        ["workspace_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_approval_instances_requester_status",
        "approval_instances",
        ["workspace_id", "requester_account_id", "status"],
        schema=schema,
    )


def _create_levels(schema: str) -> None:
    op.create_table(
        "approval_instance_levels",
        sa.Column("approval_level_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("approval_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reminder_after_minutes", sa.Integer(), nullable=False),
        sa.Column("timeout_after_minutes", sa.Integer(), nullable=False),
        sa.Column("timeout_action", sa.String(32), nullable=False),
        sa.Column(
            "fallback_approver_account_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("fallback_activated", sa.Boolean(), nullable=False),
        sa.Column("reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "approval_instance_id",
            "sequence_no",
            name="uq_approval_instance_levels_sequence",
        ),
        sa.UniqueConstraint(
            "approval_level_id",
            "approval_instance_id",
            "workspace_id",
            name="uq_approval_instance_levels_identity",
        ),
        sa.ForeignKeyConstraint(
            ["approval_instance_id", "workspace_id"],
            [
                f"{schema}.approval_instances.approval_instance_id",
                f"{schema}.approval_instances.workspace_id",
            ],
            name="fk_approval_instance_levels_instance",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "sequence_no BETWEEN 1 AND 5",
            name="ck_approval_instance_levels_sequence",
        ),
        sa.CheckConstraint("mode IN ('any', 'all')", name="ck_approval_instance_levels_mode"),
        sa.CheckConstraint(
            "status IN ('waiting', 'active', 'approved', 'rejected', 'withdrawn')",
            name="ck_approval_instance_levels_status",
        ),
        sa.CheckConstraint(
            "timeout_action IN ('escalate', 'transfer', 'reject', 'wait')",
            name="ck_approval_instance_levels_timeout_action",
        ),
        sa.CheckConstraint(
            "reminder_after_minutes >= 1 AND timeout_after_minutes > reminder_after_minutes",
            name="ck_approval_instance_levels_timeouts",
        ),
        sa.CheckConstraint("version >= 1", name="ck_approval_instance_levels_version"),
        schema=schema,
    )
    op.create_index(
        "ix_approval_instance_levels_due",
        "approval_instance_levels",
        ["workspace_id", "status", "timeout_at", "reminder_at"],
        schema=schema,
    )


def _create_assignments(schema: str) -> None:
    op.create_table(
        "approval_assignments",
        sa.Column("approval_assignment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("approval_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_level_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("transferred_to_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "approval_level_id",
            "approver_account_id",
            name="uq_approval_assignments_level_approver",
        ),
        sa.UniqueConstraint(
            "approval_assignment_id",
            "approval_instance_id",
            "workspace_id",
            name="uq_approval_assignments_identity",
        ),
        sa.ForeignKeyConstraint(
            ["approval_level_id", "approval_instance_id", "workspace_id"],
            [
                f"{schema}.approval_instance_levels.approval_level_id",
                f"{schema}.approval_instance_levels.approval_instance_id",
                f"{schema}.approval_instance_levels.workspace_id",
            ],
            name="fk_approval_assignments_level",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approver_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_approval_assignments_approver",
        ),
        sa.ForeignKeyConstraint(
            ["transferred_to_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_approval_assignments_transfer_target",
        ),
        sa.CheckConstraint(
            "status IN ('waiting', 'pending', 'approved', 'rejected', 'transferred', 'cancelled')",
            name="ck_approval_assignments_status",
        ),
        sa.CheckConstraint(
            "(status = 'transferred' AND transferred_to_account_id IS NOT NULL) OR "
            "(status != 'transferred' AND transferred_to_account_id IS NULL)",
            name="ck_approval_assignments_transfer",
        ),
        sa.CheckConstraint("version >= 1", name="ck_approval_assignments_version"),
        schema=schema,
    )
    op.create_index(
        "ix_approval_assignments_account_status",
        "approval_assignments",
        ["workspace_id", "approver_account_id", "status"],
        schema=schema,
    )


def _create_actions(schema: str) -> None:
    op.create_table(
        "approval_actions",
        sa.Column("approval_action_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("approval_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_level_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("target_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_code", sa.String(128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "approval_instance_id",
            "actor_account_id",
            "idempotency_key",
            name="uq_approval_actions_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["approval_instance_id", "workspace_id"],
            [
                f"{schema}.approval_instances.approval_instance_id",
                f"{schema}.approval_instances.workspace_id",
            ],
            name="fk_approval_actions_instance",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_level_id", "approval_instance_id", "workspace_id"],
            [
                f"{schema}.approval_instance_levels.approval_level_id",
                f"{schema}.approval_instance_levels.approval_instance_id",
                f"{schema}.approval_instance_levels.workspace_id",
            ],
            name="fk_approval_actions_level",
        ),
        sa.ForeignKeyConstraint(
            ["actor_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_approval_actions_actor",
        ),
        sa.ForeignKeyConstraint(
            ["target_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_approval_actions_target",
        ),
        sa.CheckConstraint(
            "action IN ('approve', 'reject', 'transfer', 'withdraw', 'remind', 'escalate', "
            "'timeout_transfer', 'timeout_reject', 'timeout_wait')",
            name="ck_approval_actions_action",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_approval_actions_idempotency",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_.-]{0,127}$'",
            name="ck_approval_actions_reason",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_approval_actions_instance_time",
        "approval_actions",
        ["approval_instance_id", "occurred_at"],
        schema=schema,
    )


def _protect_actions(schema: str) -> None:
    """阻断动作事实更新或删除，纠错只能追加新的业务动作。"""

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".reject_approval_action_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'approval_actions are immutable';
            END;
            $$;
            CREATE TRIGGER protect_approval_actions
            BEFORE UPDATE OR DELETE ON "{schema}".approval_actions
            FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_approval_action_mutation();
            """
        )
    )


def _backfill_permissions(schema: str) -> None:
    _seed_role_permissions(schema, "workspace_owner", OWNER_PERMISSIONS, "RESTRICTED")
    _seed_role_permissions(schema, "workspace_member", MEMBER_PERMISSIONS, "INTERNAL")


def _seed_role_permissions(
    schema: str,
    role_key: str,
    permission_codes: tuple[str, ...],
    security_level: str,
) -> None:
    permissions = ", ".join(f"('{code}')" for code in permission_codes)
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   '{security_level}', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS permission_codes(permission_code)
            WHERE roles.role_key = '{role_key}' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_bindings(schema: str) -> None:
    values = ", ".join(
        "("
        + ", ".join(
            (
                f"'82000000-0000-4000-8000-{menu_number:012d}'::uuid",
                f"'81000000-0000-4000-8000-{api_number:012d}'::uuid",
                f"'{action_type}'",
            )
        )
        + ")"
        for menu_number, api_number, action_type in APPROVAL_RUNTIME_BINDINGS
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
                menu_id, api_resource_id, action_type
            ) VALUES {values}
            ON CONFLICT (menu_id, api_resource_id) DO UPDATE
            SET action_type = EXCLUDED.action_type
            """
        )
    )
