"""定义 Agent 控制台请求与脱敏响应 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateAgentRequest(BaseModel):
    """创建 Agent 所需的定义与完整版本引用配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    configuration: dict[str, object] | None = None
    use_starter_configuration: bool = False

    @model_validator(mode="after")
    def require_one_configuration_mode(self) -> CreateAgentRequest:
        """基础配置和完整配置必须且只能选择一种，避免隐式空对象语义。"""

        if self.use_starter_configuration == (self.configuration is not None):
            raise ValueError("必须且只能选择一种 Agent 配置模式")
        return self


class UpdateAgentDraftRequest(BaseModel):
    """按乐观锁替换当前草稿配置。"""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    configuration: dict[str, object]


class ArchiveAgentRequest(BaseModel):
    """按定义版本归档 Agent，历史发布事实不删除。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class RequestAgentReleaseRequest(BaseModel):
    """冻结当前草稿 revision 为发布候选。"""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class AgentResponse(BaseModel):
    """表示控制台可见的自定义 Agent 定义。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    workspace_id: UUID
    agent_key: str
    name: str
    description: str | None
    status: Literal["active", "archived"]
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


class AgentDraftResponse(BaseModel):
    """表示当前可编辑草稿；Runtime 永远不能消费本响应。"""

    model_config = ConfigDict(extra="forbid")

    draft_id: UUID
    agent_id: UUID
    revision: int
    status: str
    configuration: dict[str, object]
    config_hash: str
    updated_at: datetime


class AgentDetailResponse(BaseModel):
    """组合 Agent 定义与当前草稿。"""

    model_config = ConfigDict(extra="forbid")

    agent: AgentResponse
    draft: AgentDraftResponse


class AgentListResponse(BaseModel):
    """返回当前工作空间的 Agent 列表。"""

    model_config = ConfigDict(extra="forbid")

    items: list[AgentDetailResponse]


class AgentCandidateResponse(BaseModel):
    """表示一次冻结草稿 revision 的发布候选。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID
    agent_id: UUID
    draft_revision: int
    candidate_hash: str
    config_hash: str
    status: str
    created_at: datetime
    updated_at: datetime
    version: int


class AgentEvaluationCheckResponse(BaseModel):
    """返回单类测试门禁的聚合结论，不暴露测试输入与回答正文。"""

    model_config = ConfigDict(extra="forbid")

    check_code: str
    status: Literal["passed", "failed"]
    case_count: int
    passed_count: int
    score_bps: int


class AgentEvaluationResponse(BaseModel):
    """表示候选最近一次不可变确定性评估。"""

    model_config = ConfigDict(extra="forbid")

    evaluation_run_id: UUID
    candidate_id: UUID
    status: Literal["passed", "failed"]
    evaluator_version: str
    evidence_level: Literal["core_functional"]
    total_cases: int
    passed_cases: int
    failed_cases: int
    timeout_cases: int
    skipped_cases: int
    result_hash: str
    completed_at: datetime
    checks: list[AgentEvaluationCheckResponse]


class AgentApprovalResponse(BaseModel):
    """投影候选当前审批身份与终态，不返回策略条件正文。"""

    model_config = ConfigDict(extra="forbid")

    approval_instance_id: UUID
    status: Literal["pending", "approved", "rejected", "withdrawn"]
    personal_owner_confirmation: bool
    completed_at: datetime | None


class AgentCandidateControlResponse(BaseModel):
    """组合候选、最近测试和审批结论，供刷新后恢复控制台。"""

    model_config = ConfigDict(extra="forbid")

    candidate: AgentCandidateResponse
    evaluation: AgentEvaluationResponse | None
    approval: AgentApprovalResponse | None


class AgentCandidateListResponse(BaseModel):
    """返回 Agent 最近候选流水。"""

    model_config = ConfigDict(extra="forbid")

    items: list[AgentCandidateControlResponse]


class AgentReleaseResponse(BaseModel):
    """表示已校验的不可变 Agent Release 摘要。"""

    model_config = ConfigDict(extra="forbid")

    release_id: UUID
    agent_id: UUID
    version: int
    candidate_id: UUID | None
    source_draft_revision: int | None
    config_hash: str
    snapshot_hash: str | None
    released_by_account_id: UUID
    released_at: datetime


class AgentReleaseListResponse(BaseModel):
    """返回 Agent 已发布版本。"""

    model_config = ConfigDict(extra="forbid")

    items: list[AgentReleaseResponse]
