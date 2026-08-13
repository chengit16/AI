from uuid import UUID

from ai_platform_api.modules.retrieval.domain.errors import (
    CitationInvalidError,
    RetrievalScopeDeniedError,
)
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    Citation,
    SearchIndex,
)


class CitationService:
    def __init__(self, index: SearchIndex) -> None:
        self._index = index

    def issue(
        self,
        scope: AuthorizedSearchScope,
        chunk_id: UUID,
        quote: str,
    ) -> Citation:
        if not scope.content_allowed:
            raise RetrievalScopeDeniedError
        normalized_quote = " ".join(quote.split())
        chunk = self._index.get_chunk(scope, chunk_id)
        if (
            chunk is None
            or not normalized_quote
            or normalized_quote not in " ".join(chunk.content.split())
        ):
            raise CitationInvalidError
        return Citation(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            index_version_id=chunk.index_version_id,
            quote=normalized_quote,
            content_hash=chunk.content_hash,
            source_position=chunk.source_position,
        )
