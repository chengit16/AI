"""定义有界查询改写、授权范围解析和候选快照领域契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.retrieval.domain.models import AuthorizedSearchScope, SearchIndex

QueryClassification = Literal["exact_lookup", "summary", "comparison", "knowledge"]
QueryVariantKind = Literal["original", "focused"]


@dataclass(frozen=True)
class RetrievalPlannerBudget:
    """限制查询变体、搜索操作、单通道候选、最终候选和总耗时。"""

    max_query_characters: int = 4_000
    max_query_variants: int = 3
    max_search_operations: int = 6
    per_channel_candidates: int = 10
    final_candidate_limit: int = 20
    rrf_constant: int = 60
    max_elapsed_ms: int = 2_000

    def __post_init__(self) -> None:
        values = (
            self.max_query_characters,
            self.max_query_variants,
            self.max_search_operations,
            self.per_channel_candidates,
            self.final_candidate_limit,
            self.rrf_constant,
            self.max_elapsed_ms,
        )
        if min(values) < 1:
            raise ValueError("检索计划预算必须全部为正整数")
        if self.max_search_operations < self.max_query_variants * 2:
            raise ValueError("搜索操作预算必须覆盖每个查询变体的关键词与向量通道")
        maximum_candidates = self.max_query_variants * self.per_channel_candidates * 2
        if self.final_candidate_limit > maximum_candidates:
            raise ValueError("最终候选预算不能超过所有检索通道的最大候选总数")


@dataclass(frozen=True)
class RetrievalRunInput:
    """保存排队 Run、原始用户问题和冻结运行组件版本。"""

    run_id: UUID
    workspace_id: UUID
    requested_by_account_id: UUID
    runtime_config_version_id: UUID
    status: str
    query: str
    embedding_model_version: str
    retrieval_strategy_version: str
    tokenizer_version: str


@dataclass(frozen=True)
class RetrievalAuthorization:
    """把 PDP 决策收敛为检索范围解析所需的最小授权事实。"""

    decision_id: UUID
    policy_version: int
    workspace_wide: bool
    department_ids: frozenset[UUID]
    account_ids: frozenset[UUID]
    resource_ids: frozenset[UUID]
    maximum_security_level: SecurityLevel
    field_mask: frozenset[str]


@dataclass(frozen=True)
class QueryVariant:
    """记录原问题或确定性聚焦变体的稳定顺序与内容摘要。"""

    sequence_no: int
    kind: QueryVariantKind
    text: str
    query_hash: str


@dataclass(frozen=True)
class RetrievalCandidateSnapshot:
    """记录模型前候选的授权来源标识、融合分数和通道命中事实。"""

    rank: int
    chunk_id: UUID
    index_version_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    content_hash: str
    source_position: dict[str, object]
    score: float
    query_hit_count: int
    keyword_hit_count: int
    vector_hit_count: int


@dataclass(frozen=True)
class RetrievalPlanSnapshot:
    """保存一次 Run 的不可变检索计划、预算、授权决策和候选结果。"""

    retrieval_plan_id: UUID
    run_id: UUID
    workspace_id: UUID
    requested_by_account_id: UUID
    original_query_hash: str
    classification: QueryClassification
    strategy_version: str
    policy_decision_id: UUID
    policy_version: int
    maximum_security_level: SecurityLevel
    field_mask: frozenset[str]
    embedding_model_version: str
    tokenizer_version: str
    budget: RetrievalPlannerBudget
    variants: tuple[QueryVariant, ...]
    candidates: tuple[RetrievalCandidateSnapshot, ...]
    search_operation_count: int
    keyword_candidate_count: int
    vector_candidate_count: int
    created_at: datetime
    completed_at: datetime
    duration_ms: int


class RetrievalPlanningRepository(Protocol):
    """在同一事务快照内读取 Run、解析索引范围并保存检索事实。"""

    def lock_run(self, run_id: UUID) -> RetrievalRunInput | None: ...

    def get_plan(self, run_id: UUID) -> RetrievalPlanSnapshot | None: ...

    def resolve_search_scope(
        self,
        run: RetrievalRunInput,
        authorization: RetrievalAuthorization,
    ) -> AuthorizedSearchScope | None: ...

    def add_plan(self, plan: RetrievalPlanSnapshot) -> None: ...


class RetrievalPlanningUnitOfWork(Protocol):
    """保证运行读取、索引检索和不可变计划快照使用同一数据库事务。"""

    @property
    def planning(self) -> RetrievalPlanningRepository: ...

    @property
    def search_index(self) -> SearchIndex: ...

    def __enter__(self) -> RetrievalPlanningUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
