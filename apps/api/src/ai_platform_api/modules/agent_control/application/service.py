"""提供稳定的 Agent 控制面应用接口，并把具体用例委托给职责模块。"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.approval import (
    AgentApprovalContext,
    get_agent_approval,
    request_agent_approval,
)
from ai_platform_api.modules.agent_control.application.candidates import (
    request_release_candidate,
)
from ai_platform_api.modules.agent_control.application.configuration_resources import (
    create_knowledge_scope_version,
    create_output_schema_version,
    create_prompt_version,
)
from ai_platform_api.modules.agent_control.application.creation import create_agent
from ai_platform_api.modules.agent_control.application.drafts import (
    list_draft_revisions,
    update_draft,
)
from ai_platform_api.modules.agent_control.application.errors import (
    AgentConfigurationInvalidError,
    AgentDeniedError,
    AgentIdempotencyConflictError,
    AgentLifecycleConflictError,
    AgentNotFoundError,
    AgentReleaseApprovalRequiredError,
    AgentTestGateFailedError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.application.evaluation import (
    create_evaluation_dataset,
    get_evaluation_report,
    require_passing_evaluation,
    run_agent_evaluation,
)
from ai_platform_api.modules.agent_control.application.lifecycle import archive_agent
from ai_platform_api.modules.agent_control.application.queries import (
    AgentCandidateControlView,
    get_agent,
    get_release,
    list_agents,
    list_candidate_controls,
    list_releases,
)
from ai_platform_api.modules.agent_control.application.releases import publish_agent_release
from ai_platform_api.modules.agent_control.application.support import configuration_digest
from ai_platform_api.modules.agent_control.domain.approval import AgentApprovalDecision
from ai_platform_api.modules.agent_control.domain.configuration import (
    AgentKnowledgeScopeVersion,
    AgentOutputSchemaVersion,
    AgentPromptVersion,
)
from ai_platform_api.modules.agent_control.domain.evaluation import (
    AgentEvaluationDatasetVersion,
    AgentEvaluationExecutor,
    AgentEvaluationReport,
)
from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlUnitOfWork,
    AgentDraft,
    AgentDraftRevision,
    AgentRelease,
    AgentReleaseCandidate,
)
from ai_platform_api.modules.workflow.application.approval_runtime import ApprovalInstanceService

__all__ = [
    "Agent",
    "AgentApprovalDecision",
    "AgentCandidateControlView",
    "AgentConfigurationInvalidError",
    "AgentControlService",
    "AgentDeniedError",
    "AgentDraft",
    "AgentEvaluationReport",
    "AgentIdempotencyConflictError",
    "AgentKnowledgeScopeVersion",
    "AgentLifecycleConflictError",
    "AgentNotFoundError",
    "AgentRelease",
    "AgentReleaseApprovalRequiredError",
    "AgentReleaseCandidate",
    "AgentTestGateFailedError",
    "AgentValidationError",
    "configuration_digest",
]


class AgentControlService:
    """管理自定义 Agent 生命周期，并阻止控制面写入系统助手事实。"""

    def __init__(
        self,
        unit_of_work: AgentControlUnitOfWork,
        evaluation_executor: AgentEvaluationExecutor | None = None,
        approval_instances: ApprovalInstanceService | None = None,
        runtime_bootstrap: Callable[[UUID], object] | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._evaluation_executor = evaluation_executor
        self._approval_instances = approval_instances
        self._runtime_bootstrap = runtime_bootstrap

    def create_prompt_version(
        self,
        context: RequestContext,
        *,
        name: str,
        template: str,
    ) -> AgentPromptVersion:
        """创建内容寻址的不可变 Prompt 版本，明文凭证不会进入事实库。"""

        return create_prompt_version(
            self._unit_of_work,
            context,
            name=name,
            template=template,
        )

    def create_knowledge_scope_version(
        self,
        context: RequestContext,
        *,
        name: str,
        knowledge_base_ids: tuple[UUID, ...],
    ) -> AgentKnowledgeScopeVersion:
        """冻结当前主体可访问的知识库集合，后续仍会复核状态与权限。"""

        return create_knowledge_scope_version(
            self._unit_of_work,
            context,
            name=name,
            knowledge_base_ids=knowledge_base_ids,
        )

    def create_output_schema_version(
        self,
        context: RequestContext,
        *,
        name: str,
        schema_document: dict[str, object],
    ) -> AgentOutputSchemaVersion:
        """校验并冻结结构化输出 Schema，供草稿只按版本引用。"""

        return create_output_schema_version(
            self._unit_of_work,
            context,
            name=name,
            schema_document=schema_document,
        )

    def create_agent(
        self,
        context: RequestContext,
        *,
        name: str,
        description: str | None,
        configuration: dict[str, object] | None,
        use_starter_configuration: bool = False,
        idempotency_key: str,
    ) -> tuple[Agent, AgentDraft]:
        """原子创建自定义 Agent、首个草稿修订及可靠变更事件。"""

        return create_agent(
            self._unit_of_work,
            context,
            name=name,
            description=description,
            configuration=configuration,
            use_starter_configuration=use_starter_configuration,
            runtime_bootstrap=self._runtime_bootstrap,
            idempotency_key=idempotency_key,
        )

    def list_agents(
        self,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> tuple[tuple[Agent, AgentDraft, tuple[AgentKnowledgeScopeVersion, ...]], ...]:
        """列出当前空间可管理的自定义 Agent、草稿和知识范围。"""

        return list_agents(self._unit_of_work, context, limit=limit)

    def get_agent(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
    ) -> tuple[Agent, AgentDraft]:
        """读取当前定义和草稿；系统 Agent 对控制面保持不可见。"""

        return get_agent(self._unit_of_work, context, agent_id=agent_id)

    def update_draft(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        expected_revision: int,
        configuration: dict[str, object],
        knowledge_scope_name: str | None = None,
        knowledge_base_ids: tuple[UUID, ...] | None = None,
        idempotency_key: str,
    ) -> AgentDraft:
        """用乐观锁写入新草稿 revision，并保留旧修订的完整可追溯事实。"""

        return update_draft(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            expected_revision=expected_revision,
            configuration=configuration,
            knowledge_scope_name=knowledge_scope_name,
            knowledge_base_ids=knowledge_base_ids,
            idempotency_key=idempotency_key,
        )

    def list_draft_revisions(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        limit: int,
    ) -> tuple[AgentDraftRevision, ...]:
        """按 revision 倒序返回 Agent 草稿历史，不跨工作空间或资源范围。"""

        return list_draft_revisions(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            limit=limit,
        )

    def request_release_candidate(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        expected_revision: int,
        idempotency_key: str,
    ) -> AgentReleaseCandidate:
        """冻结候选来源；本节点不伪造测试、审批或已发布结论。"""

        return request_release_candidate(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def list_candidate_controls(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        limit: int = 50,
    ) -> tuple[AgentCandidateControlView, ...]:
        """列出候选、最近确定性测试和审批状态，供页面刷新恢复。"""

        return list_candidate_controls(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            limit=limit,
        )

    def create_evaluation_dataset(
        self,
        context: RequestContext,
        *,
        name: str,
        dataset_version: str,
        cases: tuple[dict[str, object], ...],
    ) -> AgentEvaluationDatasetVersion:
        """冻结覆盖五类必需检查的合成测试集，相同版本不能替换内容。"""

        return create_evaluation_dataset(
            self._unit_of_work,
            context,
            name=name,
            dataset_version=dataset_version,
            cases=cases,
        )

    def run_evaluation(
        self,
        context: RequestContext,
        *,
        candidate_id: UUID,
        dataset_version_id: UUID,
    ) -> AgentEvaluationReport:
        """通过内部执行器运行固定测试，并保存不可变评估结果。"""

        if self._evaluation_executor is None:
            raise AgentTestGateFailedError
        return run_agent_evaluation(
            self._unit_of_work,
            self._evaluation_executor,
            context,
            candidate_id=candidate_id,
            dataset_version_id=dataset_version_id,
        )

    def run_release_gate_evaluation(
        self,
        context: RequestContext,
        *,
        candidate_id: UUID,
    ) -> AgentEvaluationReport:
        """运行平台固定的五类合成门禁，不接受浏览器上传测试观测。"""

        dataset = self.create_evaluation_dataset(
            context,
            name="Agent 发布固定门禁",
            dataset_version="p304-console-release-gate-v1",
            cases=_release_gate_cases(),
        )
        return self.run_evaluation(
            context,
            candidate_id=candidate_id,
            dataset_version_id=dataset.dataset_version_id,
        )

    def get_evaluation_report(
        self,
        context: RequestContext,
        *,
        candidate_id: UUID,
        evaluation_run_id: UUID,
    ) -> AgentEvaluationReport:
        """读取候选的不可变评估报告，不返回测试输入、回答或异常正文。"""

        return get_evaluation_report(
            self._unit_of_work,
            context,
            candidate_id=candidate_id,
            evaluation_run_id=evaluation_run_id,
        )

    def require_passing_evaluation(
        self,
        context: RequestContext,
        *,
        candidate_id: UUID,
    ) -> AgentEvaluationReport:
        """为后续审批门禁读取与候选摘要一致的完整通过证据。"""

        return require_passing_evaluation(
            self._unit_of_work,
            context,
            candidate_id=candidate_id,
        )

    def request_approval(
        self,
        context: RequestContext,
        *,
        candidate_id: UUID,
        idempotency_key: str,
    ) -> AgentApprovalContext:
        """为已通过固定测试的候选发起个人所有者或企业多级审批。"""

        if self._approval_instances is None:
            raise AgentReleaseApprovalRequiredError
        return request_agent_approval(
            self._unit_of_work,
            self._approval_instances,
            context,
            candidate_id=candidate_id,
            idempotency_key=idempotency_key,
        )

    def get_approval(
        self,
        context: RequestContext,
        *,
        candidate_id: UUID,
    ) -> AgentApprovalContext:
        """读取候选绑定的审批上下文和当前多级审批状态。"""

        if self._approval_instances is None:
            raise AgentReleaseApprovalRequiredError
        return get_agent_approval(
            self._unit_of_work,
            self._approval_instances,
            context,
            candidate_id=candidate_id,
        )

    def archive_agent(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        expected_version: int,
        idempotency_key: str,
    ) -> Agent:
        """归档自定义 Agent 并终止当前草稿，历史候选和 Release 保持可追溯。"""

        return archive_agent(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def publish_release(
        self,
        context: RequestContext,
        *,
        candidate_id: UUID,
        idempotency_key: str,
    ) -> AgentRelease:
        """把已通过测试和审批的当前候选固化为不可变 Release。"""

        return publish_agent_release(
            self._unit_of_work,
            context,
            candidate_id=candidate_id,
            idempotency_key=idempotency_key,
        )

    def list_releases(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        limit: int = 50,
    ) -> tuple[AgentRelease, ...]:
        """列出已校验的不可变 Release，控制台不能从草稿拼装发布快照。"""

        return list_releases(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            limit=limit,
        )

    def get_release(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        release_id: UUID,
    ) -> AgentRelease:
        """读取自定义 Agent 的不可变 Release，不回读当前草稿补齐快照。"""

        return get_release(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            release_id=release_id,
        )


def _release_gate_cases() -> tuple[dict[str, object], ...]:
    """返回覆盖功能、越权、注入、引用和输出契约的最小全合成测试集。"""

    check_codes = (
        "functional",
        "authorization",
        "prompt_injection",
        "citation",
        "output_contract",
    )
    return tuple(
        {
            "case_key": f"console.synthetic.{check_code}",
            "check_code": check_code,
            "input_fixture": {"fixture": f"SYNTHETIC_{check_code.upper()}"},
            "expected_fixture": {"decision": "allow"},
            "timeout_ms": 2_000,
            "minimum_score_bps": 8_000 if check_code == "functional" else 10_000,
        }
        for check_code in check_codes
    )
