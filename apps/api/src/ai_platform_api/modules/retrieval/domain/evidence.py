"""定义重排、来源排序、受控精读和不可变证据快照契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    SearchIndex,
    SecurityLevel,
    StoredChunk,
)
from ai_platform_api.modules.retrieval.domain.planning import (
    RetrievalCandidateSnapshot,
    RetrievalPlanningRepository,
)

SourceKind = Literal["manual", "upload", "web", "data_source"]
EvidenceStatus = Literal["sufficient", "uncertain"]
EvidenceDegradationReason = Literal[
    "no_candidates",
    "no_current_evidence",
    "insufficient_sources",
    "conflicting_evidence",
]


@dataclass(frozen=True)
class EvidenceProcessingBudget:
    """限制重排候选、精读文档、上下文字符、Token 和总耗时。"""

    rerank_candidate_limit: int = 10
    final_evidence_limit: int = 5
    max_documents: int = 3
    surrounding_chunks: int = 1
    max_chunks: int = 12
    max_characters: int = 12_000
    max_tokens: int = 4_000
    max_elapsed_ms: int = 2_000
    fastpass_score_ratio: float = 1.2
    minimum_final_score: float = 0.25
    max_quote_characters: int = 1_000

    def __post_init__(self) -> None:
        integer_values = (
            self.rerank_candidate_limit,
            self.final_evidence_limit,
            self.max_documents,
            self.max_chunks,
            self.max_characters,
            self.max_tokens,
            self.max_elapsed_ms,
            self.max_quote_characters,
        )
        if min(integer_values) < 1 or self.surrounding_chunks < 0:
            raise ValueError("证据处理预算必须为有效正数")
        if self.final_evidence_limit > self.rerank_candidate_limit:
            raise ValueError("最终证据数不能超过重排候选数")
        if self.max_documents > self.final_evidence_limit:
            raise ValueError("精读文档数不能超过最终证据数")
        if self.fastpass_score_ratio < 1:
            raise ValueError("FastPass 分数倍率不能小于 1")
        if not 0 <= self.minimum_final_score <= 1:
            raise ValueError("最低最终分数必须位于 0 到 1")


@dataclass(frozen=True)
class EvidenceCandidateSource:
    """保存当前仍授权的候选正文及其来源、版本和发布时间事实。"""

    candidate: RetrievalCandidateSnapshot
    chunk: StoredChunk
    document_title: str
    source_kind: SourceKind
    source_name: str
    captured_at: datetime | None
    published_at: datetime


@dataclass(frozen=True)
class RankedEvidenceCandidate:
    """汇总检索、重排、来源权威性和时效性分数。"""

    source: EvidenceCandidateSource
    relevance_score: float
    authority_score: float
    freshness_score: float
    final_score: float


@dataclass(frozen=True)
class EvidenceItemSnapshot:
    """保存经过精读和引用校验的原文、上下文及稳定来源标识。"""

    rank: int
    chunk_id: UUID
    index_version_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    content_hash: str
    quote: str
    context_text: str
    context_hash: str
    context_chunk_ids: tuple[UUID, ...]
    source_position: dict[str, object]
    document_title: str
    source_kind: SourceKind
    source_name: str
    security_level: SecurityLevel
    retrieval_score: float
    relevance_score: float
    authority_score: float
    freshness_score: float
    final_score: float
    conflict_detected: bool


@dataclass(frozen=True)
class EvidenceSetSnapshot:
    """记录一次 Run 的不可变证据选择、降级原因和冻结组件版本。"""

    evidence_set_id: UUID
    retrieval_plan_id: UUID
    run_id: UUID
    workspace_id: UUID
    requested_by_account_id: UUID
    status: EvidenceStatus
    degradation_reason: EvidenceDegradationReason | None
    policy_decision_id: UUID
    policy_version: int
    reranker_model_version: str
    source_ranking_version: str
    fastpass_used: bool
    reranker_used: bool
    candidate_count: int
    rejected_candidate_count: int
    conflict_count: int
    read_document_count: int
    read_chunk_count: int
    read_character_count: int
    estimated_token_count: int
    budget: EvidenceProcessingBudget
    items: tuple[EvidenceItemSnapshot, ...]
    created_at: datetime
    completed_at: datetime
    duration_ms: int


class EvidenceRepository(Protocol):
    """读取当前来源元数据并只追加一次证据处理结果。"""

    def get_evidence_set(self, run_id: UUID) -> EvidenceSetSnapshot | None: ...

    def load_candidate_source(
        self,
        scope: AuthorizedSearchScope,
        candidate: RetrievalCandidateSnapshot,
    ) -> EvidenceCandidateSource | None: ...

    def evidence_set_is_current(
        self,
        scope: AuthorizedSearchScope,
        evidence_set: EvidenceSetSnapshot,
    ) -> bool: ...

    def add_evidence_set(self, evidence_set: EvidenceSetSnapshot) -> None: ...


class EvidenceProcessingUnitOfWork(Protocol):
    """保证运行、当前授权来源、精读和证据快照位于同一事务。"""

    @property
    def planning(self) -> RetrievalPlanningRepository: ...

    @property
    def evidence(self) -> EvidenceRepository: ...

    @property
    def search_index(self) -> SearchIndex: ...

    def __enter__(self) -> EvidenceProcessingUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
