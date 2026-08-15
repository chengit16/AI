"""建立 P3-05 Agent 审批证据绑定、候选失效和数据库防绕过门禁。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0045"
down_revision: str | None = "20260816_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERSONAL_POLICY_VERSION_ID = "a5000000-0000-4000-8000-000000000305"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_bindings(schema)
    _protect_bindings(schema)
    _replace_candidate_transition(schema, require_approval=True)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _replace_candidate_transition(schema, require_approval=False)
    _drop_binding_protection(schema)
    op.drop_table("agent_approval_bindings", schema=schema)


def _create_bindings(schema: str) -> None:
    op.create_table(
        "agent_approval_bindings",
        sa.Column("approval_binding_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "evaluation_policy_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("approval_policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("evaluation_result_hash", sa.String(64), nullable=False),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("chain_digest", sa.String(64), nullable=False),
        sa.Column("personal_owner_confirmation", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "approval_binding_id",
            "workspace_id",
            name="uq_agent_approval_bindings_id_workspace",
        ),
        sa.UniqueConstraint("candidate_id", name="uq_agent_approval_bindings_candidate"),
        sa.UniqueConstraint(
            "approval_instance_id",
            name="uq_agent_approval_bindings_instance",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "workspace_id"],
            [
                f"{schema}.agent_release_candidates.candidate_id",
                f"{schema}.agent_release_candidates.workspace_id",
            ],
            name="fk_agent_approval_bindings_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            [f"{schema}.agents.agent_id", f"{schema}.agents.workspace_id"],
            name="fk_agent_approval_bindings_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_instance_id", "workspace_id"],
            [
                f"{schema}.approval_instances.approval_instance_id",
                f"{schema}.approval_instances.workspace_id",
            ],
            name="fk_agent_approval_bindings_instance",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id", "workspace_id"],
            [
                f"{schema}.agent_evaluation_runs.evaluation_run_id",
                f"{schema}.agent_evaluation_runs.workspace_id",
            ],
            name="fk_agent_approval_bindings_evaluation",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_policy_version_id"],
            [f"{schema}.agent_evaluation_policy_versions.evaluation_policy_version_id"],
            name="fk_agent_approval_bindings_evaluation_policy",
        ),
        sa.CheckConstraint(
            "candidate_hash ~ '^[0-9a-f]{64}$' "
            "AND config_hash ~ '^[0-9a-f]{64}$' "
            "AND evaluation_result_hash ~ '^[0-9a-f]{64}$' "
            "AND subject_digest ~ '^[0-9a-f]{64}$' "
            "AND chain_digest ~ '^[0-9a-f]{64}$'",
            name="ck_agent_approval_bindings_hashes",
        ),
        sa.CheckConstraint(
            "personal_owner_confirmation = false OR "
            f"approval_policy_version_id = '{PERSONAL_POLICY_VERSION_ID}'::uuid",
            name="ck_agent_approval_bindings_personal_policy",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_agent_approval_bindings_workspace_time",
        "agent_approval_bindings",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _protect_bindings(schema: str) -> None:
    # 插入时复核候选、评估、审批实例和当前草稿，避免先伪造绑定再推进候选。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".validate_agent_approval_binding()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".agent_release_candidates candidate
                    JOIN "{schema}".agent_drafts draft
                      ON draft.draft_id = candidate.draft_id
                     AND draft.workspace_id = candidate.workspace_id
                    JOIN "{schema}".agent_evaluation_runs evaluation
                      ON evaluation.evaluation_run_id = NEW.evaluation_run_id
                     AND evaluation.workspace_id = NEW.workspace_id
                     AND evaluation.candidate_id = candidate.candidate_id
                    JOIN "{schema}".approval_instances approval
                      ON approval.approval_instance_id = NEW.approval_instance_id
                     AND approval.workspace_id = NEW.workspace_id
                    WHERE candidate.candidate_id = NEW.candidate_id
                      AND candidate.workspace_id = NEW.workspace_id
                      AND candidate.agent_id = NEW.agent_id
                      AND candidate.status = 'ready_for_approval'
                      AND candidate.candidate_hash = NEW.candidate_hash
                      AND candidate.config_hash = NEW.config_hash
                      AND candidate.created_by_account_id = approval.requester_account_id
                      AND draft.revision = candidate.draft_revision
                      AND draft.config_hash = candidate.config_hash
                      AND evaluation.evaluation_policy_version_id
                          = NEW.evaluation_policy_version_id
                      AND evaluation.candidate_hash = NEW.candidate_hash
                      AND evaluation.config_hash = NEW.config_hash
                      AND evaluation.result_hash = NEW.evaluation_result_hash
                      AND evaluation.status = 'passed'
                      AND evaluation.passed_cases = evaluation.total_cases
                      AND approval.resource_type = 'agent.release'
                      AND approval.operation = 'approve'
                      AND approval.resource_id = NEW.candidate_id
                      AND approval.status = 'pending'
                      AND approval.subject_digest = NEW.subject_digest
                      AND approval.chain_digest = NEW.chain_digest
                      AND approval.personal_owner_confirmation
                          = NEW.personal_owner_confirmation
                      AND (
                          (approval.personal_owner_confirmation = true
                           AND approval.approval_policy_version_id IS NULL
                           AND NEW.approval_policy_version_id
                               = '{PERSONAL_POLICY_VERSION_ID}'::uuid) OR
                          (approval.personal_owner_confirmation = false
                           AND approval.approval_policy_version_id
                               = NEW.approval_policy_version_id)
                      )
                      AND (
                          SELECT count(*)
                          FROM "{schema}".agent_evaluation_check_results result
                          WHERE result.evaluation_run_id = evaluation.evaluation_run_id
                            AND result.workspace_id = evaluation.workspace_id
                            AND result.status = 'passed'
                      ) = 5
                      AND NOT EXISTS (
                          SELECT 1
                          FROM "{schema}".agent_evaluation_case_results result
                          WHERE result.evaluation_run_id = evaluation.evaluation_run_id
                            AND result.workspace_id = evaluation.workspace_id
                            AND result.outcome <> 'passed'
                      )
                ) THEN
                    RAISE EXCEPTION 'invalid agent approval binding' USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_agent_approval_bindings_validate
            BEFORE INSERT ON "{schema}"."agent_approval_bindings"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_agent_approval_binding()
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".reject_agent_approval_binding_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   AND current_setting('ai_platform.lifecycle_purge', true) = 'on'
                THEN RETURN OLD;
                END IF;
                RAISE EXCEPTION 'agent approval binding is immutable' USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_agent_approval_bindings_immutable
            BEFORE UPDATE OR DELETE ON "{schema}"."agent_approval_bindings"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_agent_approval_binding_mutation()
            """
        )
    )


def _replace_candidate_transition(schema: str, *, require_approval: bool) -> None:
    op.execute(
        sa.text(
            f"DROP TRIGGER trg_agent_release_candidates_transition "
            f'ON "{schema}"."agent_release_candidates"'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".enforce_agent_candidate_transition()'))
    approval_guards = _approval_transition_guards(schema) if require_approval else ""
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".enforce_agent_candidate_transition()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.version <> OLD.version + 1 OR NEW.updated_at < OLD.updated_at THEN
                    RAISE EXCEPTION 'invalid agent candidate version transition'
                        USING ERRCODE = '55000';
                END IF;
                IF NOT (
                    (OLD.status = 'created'
                     AND NEW.status IN ('testing', 'test_failed', 'ready_for_approval')) OR
                    (OLD.status = 'testing'
                     AND NEW.status IN ('test_failed', 'ready_for_approval')) OR
                    (OLD.status = 'test_failed'
                     AND NEW.status IN ('testing', 'test_failed', 'ready_for_approval')) OR
                    (OLD.status = 'ready_for_approval'
                     AND NEW.status IN (
                        'ready_for_approval', 'test_failed', 'approval_pending', 'superseded'
                     )) OR
                    (OLD.status = 'approval_pending'
                     AND NEW.status IN ('approved', 'rejected', 'superseded')) OR
                    (OLD.status = 'approved' AND NEW.status IN ('released', 'superseded')) OR
                    (OLD.status = 'rejected' AND NEW.status = 'superseded')
                ) THEN
                    RAISE EXCEPTION 'invalid agent candidate status transition'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.status = 'ready_for_approval' AND NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".agent_evaluation_runs run
                    JOIN "{schema}".agent_evaluation_policy_versions policy
                      ON policy.evaluation_policy_version_id = run.evaluation_policy_version_id
                    WHERE run.candidate_id = NEW.candidate_id
                      AND run.workspace_id = NEW.workspace_id
                      AND run.candidate_hash = NEW.candidate_hash
                      AND run.config_hash = NEW.config_hash
                      AND run.status = 'passed'
                      AND run.passed_cases = run.total_cases
                      AND (
                          SELECT count(*)
                          FROM "{schema}".agent_evaluation_check_results result
                          WHERE result.evaluation_run_id = run.evaluation_run_id
                            AND result.workspace_id = run.workspace_id
                            AND result.status = 'passed'
                            AND result.score_bps >= (
                                policy.minimum_check_scores ->> result.check_code
                            )::integer
                      ) = jsonb_array_length(policy.required_check_codes)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM "{schema}".agent_evaluation_case_results case_result
                          WHERE case_result.evaluation_run_id = run.evaluation_run_id
                            AND case_result.workspace_id = run.workspace_id
                            AND case_result.outcome <> 'passed'
                      )
                ) THEN
                    RAISE EXCEPTION 'passing agent evaluation evidence is required'
                        USING ERRCODE = '55000';
                END IF;
                {approval_guards}
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_agent_release_candidates_transition
            BEFORE UPDATE ON "{schema}"."agent_release_candidates"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".enforce_agent_candidate_transition()
            """
        )
    )


def _approval_transition_guards(schema: str) -> str:
    return f"""
                IF NEW.status = 'approval_pending' AND NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".agent_approval_bindings binding
                    JOIN "{schema}".approval_instances approval
                      ON approval.approval_instance_id = binding.approval_instance_id
                     AND approval.workspace_id = binding.workspace_id
                    WHERE binding.candidate_id = NEW.candidate_id
                      AND binding.workspace_id = NEW.workspace_id
                      AND binding.agent_id = NEW.agent_id
                      AND binding.candidate_hash = NEW.candidate_hash
                      AND binding.config_hash = NEW.config_hash
                      AND approval.status = 'pending'
                      AND approval.resource_id = NEW.candidate_id
                      AND approval.subject_digest = binding.subject_digest
                      AND approval.chain_digest = binding.chain_digest
                ) THEN
                    RAISE EXCEPTION 'agent approval binding is required'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.status = 'approved' AND NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".agent_approval_bindings binding
                    JOIN "{schema}".approval_instances approval
                      ON approval.approval_instance_id = binding.approval_instance_id
                     AND approval.workspace_id = binding.workspace_id
                    JOIN "{schema}".agent_drafts draft
                      ON draft.draft_id = NEW.draft_id
                     AND draft.workspace_id = NEW.workspace_id
                    WHERE binding.candidate_id = NEW.candidate_id
                      AND binding.workspace_id = NEW.workspace_id
                      AND binding.candidate_hash = NEW.candidate_hash
                      AND binding.config_hash = NEW.config_hash
                      AND approval.status = 'approved'
                      AND approval.subject_digest = binding.subject_digest
                      AND approval.chain_digest = binding.chain_digest
                      AND draft.revision = NEW.draft_revision
                      AND draft.config_hash = NEW.config_hash
                ) THEN
                    RAISE EXCEPTION 'approved agent approval evidence is required'
                        USING ERRCODE = '55000';
                END IF;
                IF NEW.status = 'rejected' AND NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".agent_approval_bindings binding
                    JOIN "{schema}".approval_instances approval
                      ON approval.approval_instance_id = binding.approval_instance_id
                     AND approval.workspace_id = binding.workspace_id
                    WHERE binding.candidate_id = NEW.candidate_id
                      AND binding.workspace_id = NEW.workspace_id
                      AND approval.status = 'rejected'
                ) THEN
                    RAISE EXCEPTION 'rejected agent approval evidence is required'
                        USING ERRCODE = '55000';
                END IF;
    """


def _drop_binding_protection(schema: str) -> None:
    op.execute(
        sa.text(
            f"DROP TRIGGER trg_agent_approval_bindings_immutable "
            f'ON "{schema}"."agent_approval_bindings"'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".reject_agent_approval_binding_mutation()'))
    op.execute(
        sa.text(
            f"DROP TRIGGER trg_agent_approval_bindings_validate "
            f'ON "{schema}"."agent_approval_bindings"'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".validate_agent_approval_binding()'))


def _reject_unsafe_downgrade(schema: str) -> None:
    count = (
        op.get_bind()
        .execute(sa.text(f'SELECT count(*) FROM "{schema}"."agent_approval_bindings"'))
        .scalar_one()
    )
    if int(count) > 0:
        raise RuntimeError("存在 Agent 审批绑定, 拒绝降级并静默丢失数据")
