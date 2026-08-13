from dataclasses import dataclass, replace
from typing import Literal, Protocol
from uuid import UUID

Visibility = Literal["private", "workspace", "departments", "public"]
SecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
SearchChannel = Literal["keyword", "vector"]


@dataclass(frozen=True)
class AuthorizedSearchScope:
    """由策略决策转换出的可执行范围，所有数据入口必须完整应用。"""

    workspace_id: UUID
    index_version_ids: frozenset[UUID]
    knowledge_base_ids: frozenset[UUID] | None
    document_ids: frozenset[UUID] | None
    department_ids: frozenset[UUID]
    visibilities: frozenset[Visibility]
    security_levels: frozenset[SecurityLevel]
    field_mask: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.index_version_ids:
            raise ValueError("授权检索范围必须包含至少一个索引版本")
        if not self.visibilities or not self.security_levels:
            raise ValueError("授权检索范围必须包含可见性和密级条件")
        # 私有文档不能只依赖工作空间隔离，策略必须明确给出当前主体可读的文档集合。
        if "private" in self.visibilities and self.document_ids is None:
            raise ValueError("私有文档检索必须包含明确的文档授权范围")

    @property
    def content_allowed(self) -> bool:
        return "content" not in self.field_mask


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_version_id: UUID
    sequence_no: int
    content: str
    content_hash: str
    embedding: tuple[float, ...]
    keyword_text: str
    department_ids: tuple[UUID, ...]
    visibility: Visibility
    security_level: SecurityLevel
    source_position: dict[str, object]
    active: bool = True


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_version_id: UUID
    sequence_no: int
    content: str
    content_hash: str
    source_position: dict[str, object]

    def apply_field_mask(self, field_mask: frozenset[str]) -> "StoredChunk":
        """正文受限时由调用方整体拒绝；可独立隐藏的来源元数据在离开责任模块前清空。"""

        if "content" in field_mask:
            raise ValueError("正文受限时不能投影 Chunk")
        if "source_position" in field_mask:
            return replace(self, source_position={})
        return self


@dataclass(frozen=True)
class ChannelCandidate:
    chunk: StoredChunk
    channel: SearchChannel
    rank: int
    score: float


@dataclass(frozen=True)
class SearchCandidate:
    chunk: StoredChunk
    keyword_rank: int | None
    vector_rank: int | None
    keyword_score: float | None
    vector_score: float | None
    hybrid_score: float
    rerank_score: float | None = None


@dataclass(frozen=True)
class RetrievalBudget:
    per_channel_candidates: int = 20
    rerank_candidates: int = 10
    final_candidates: int = 5
    rrf_constant: int = 60
    fastpass_vector_score: float = 0.82

    def __post_init__(self) -> None:
        if (
            min(
                self.per_channel_candidates,
                self.rerank_candidates,
                self.final_candidates,
                self.rrf_constant,
            )
            < 1
        ):
            raise ValueError("检索候选数和 RRF 常量必须为正整数")
        if self.rerank_candidates > self.per_channel_candidates * 2:
            raise ValueError("重排候选数不能超过两个通道的候选总数")
        if self.final_candidates > self.rerank_candidates:
            raise ValueError("最终候选数不能超过重排候选数")
        if not 0 <= self.fastpass_vector_score <= 1:
            raise ValueError("FastPass 向量阈值必须位于 0 到 1")


@dataclass(frozen=True)
class Evidence:
    chunk: StoredChunk
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    evidence: tuple[Evidence, ...]
    reranker_used: bool
    keyword_candidate_count: int
    vector_candidate_count: int
    embedding_model_version: str
    reranker_model_version: str | None
    tokenizer_version: str


@dataclass(frozen=True)
class Citation:
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_version_id: UUID
    quote: str
    content_hash: str
    source_position: dict[str, object]


class EmbeddingProvider(Protocol):
    model_version: str
    dimension: int

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class Reranker(Protocol):
    model_version: str

    def score(self, query: str, passages: tuple[str, ...]) -> tuple[float, ...]: ...


class SearchIndex(Protocol):
    def keyword_search(
        self,
        scope: AuthorizedSearchScope,
        keyword_query: str,
        limit: int,
    ) -> tuple[ChannelCandidate, ...]: ...

    def vector_search(
        self,
        scope: AuthorizedSearchScope,
        embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[ChannelCandidate, ...]: ...

    def read_document_range(
        self,
        scope: AuthorizedSearchScope,
        document_version_id: UUID,
        sequence_start: int,
        sequence_end: int,
    ) -> tuple[StoredChunk, ...]: ...

    def get_chunk(
        self,
        scope: AuthorizedSearchScope,
        chunk_id: UUID,
    ) -> StoredChunk | None: ...
