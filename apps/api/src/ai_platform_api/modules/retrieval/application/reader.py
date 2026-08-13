from dataclasses import dataclass
from uuid import UUID

from ai_platform_api.modules.retrieval.domain.errors import RetrievalScopeDeniedError
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    SearchIndex,
    StoredChunk,
)


@dataclass(frozen=True)
class ReaderBudget:
    surrounding_chunks: int
    max_chunks: int
    max_characters: int

    def __post_init__(self) -> None:
        if self.surrounding_chunks < 0 or self.max_chunks < 1 or self.max_characters < 1:
            raise ValueError("全文精读预算必须为有效正数")


class AuthorizedDocumentReader:
    def __init__(self, index: SearchIndex) -> None:
        self._index = index

    def read(
        self,
        scope: AuthorizedSearchScope,
        document_version_id: UUID,
        center_sequence_no: int,
        budget: ReaderBudget,
    ) -> tuple[StoredChunk, ...]:
        if not scope.content_allowed:
            raise RetrievalScopeDeniedError
        sequence_start = max(1, center_sequence_no - budget.surrounding_chunks)
        sequence_end = center_sequence_no + budget.surrounding_chunks
        chunks = self._index.read_document_range(
            scope,
            document_version_id,
            sequence_start,
            sequence_end,
        )
        if not any(chunk.sequence_no == center_sequence_no for chunk in chunks):
            return ()

        # 先按距中心的远近消耗预算，保证较小预算不会只返回前置相邻块而遗漏证据本身。
        prioritized = sorted(
            chunks,
            key=lambda chunk: (abs(chunk.sequence_no - center_sequence_no), chunk.sequence_no),
        )
        selected: list[StoredChunk] = []
        used_characters = 0
        for chunk in prioritized:
            if len(selected) >= budget.max_chunks:
                break
            if used_characters + len(chunk.content) > budget.max_characters:
                continue
            selected.append(chunk)
            used_characters += len(chunk.content)
        return tuple(sorted(selected, key=lambda chunk: chunk.sequence_no))
