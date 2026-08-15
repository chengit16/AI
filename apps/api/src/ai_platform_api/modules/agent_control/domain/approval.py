"""定义 Agent 发布候选与通用审批实例之间的不可变证据绑定。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

PERSONAL_OWNER_APPROVAL_POLICY_VERSION_ID = UUID("a5000000-0000-4000-8000-000000000305")


@dataclass(frozen=True)
class AgentApprovalBinding:
    """冻结候选、测试结果、审批策略版本和审批链摘要的同一审批上下文。"""

    approval_binding_id: UUID
    workspace_id: UUID
    candidate_id: UUID
    agent_id: UUID
    approval_instance_id: UUID
    evaluation_run_id: UUID
    evaluation_policy_version_id: UUID
    approval_policy_version_id: UUID
    candidate_hash: str
    config_hash: str
    evaluation_result_hash: str
    subject_digest: str
    chain_digest: str
    personal_owner_confirmation: bool
    created_at: datetime


class AgentApprovalRepository(Protocol):
    """按工作空间读取不可变 Agent 审批绑定，不直接解释审批运行状态。"""

    def get_by_candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AgentApprovalBinding | None: ...

    def get_by_instance(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
    ) -> AgentApprovalBinding | None: ...
