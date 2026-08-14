from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_api.modules.knowledge.domain.models import (
    DocumentSource,
    DocumentSourceKind,
    DocumentVersion,
    InvalidDocumentVersionTransitionError,
    InvalidKnowledgeFactError,
    VisibilityPolicy,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000101")
DOCUMENT_ID = UUID("40000000-0000-4000-8000-000000000101")
VERSION_ID = UUID("41000000-0000-4000-8000-000000000101")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000101")
DEPARTMENT_ID = UUID("30000000-0000-4000-8000-000000000101")
NOW = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
CONTENT_HASH = "a" * 64


def draft_version() -> DocumentVersion:
    return DocumentVersion(
        document_version_id=VERSION_ID,
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_ID,
        version_number=1,
        status="draft",
        content_hash=None,
        created_by_account_id=ACCOUNT_ID,
        created_at=NOW,
    )


def test_document_version_only_allows_forward_publication_transitions() -> None:
    draft = draft_version()
    ready = draft.mark_ready(content_hash=CONTENT_HASH)
    published = ready.publish(occurred_at=NOW)
    superseded = published.supersede()

    assert (draft.status, ready.status, published.status, superseded.status) == (
        "draft",
        "ready",
        "published",
        "superseded",
    )
    assert superseded.content_hash == CONTENT_HASH
    assert superseded.published_at == NOW
    assert superseded.record_version == 4

    with pytest.raises(InvalidDocumentVersionTransitionError):
        draft.publish(occurred_at=NOW)
    with pytest.raises(InvalidDocumentVersionTransitionError):
        ready.mark_ready(content_hash=CONTENT_HASH)
    with pytest.raises(InvalidDocumentVersionTransitionError):
        published.publish(occurred_at=NOW)
    with pytest.raises(InvalidDocumentVersionTransitionError):
        superseded.supersede()


@pytest.mark.parametrize("content_hash", ["A" * 64, "a" * 63, "g" * 64])
def test_ready_version_rejects_noncanonical_content_hash(content_hash: str) -> None:
    with pytest.raises(InvalidKnowledgeFactError):
        draft_version().mark_ready(content_hash=content_hash)


def test_visibility_policy_requires_exact_department_shape() -> None:
    VisibilityPolicy("departments", frozenset({DEPARTMENT_ID})).assert_valid()
    VisibilityPolicy("workspace").assert_valid()

    with pytest.raises(InvalidKnowledgeFactError):
        VisibilityPolicy("departments").assert_valid()
    with pytest.raises(InvalidKnowledgeFactError):
        VisibilityPolicy("private", frozenset({DEPARTMENT_ID})).assert_valid()


@dataclass(frozen=True)
class SourceCase:
    kind: DocumentSourceKind
    object_key: str | None = None
    source_url: str | None = None
    external_source_id: str | None = None
    valid: bool = True


@pytest.mark.parametrize(
    "case",
    [
        SourceCase("manual"),
        SourceCase("upload", object_key="synthetic/workspace/document.bin"),
        SourceCase("web", source_url="https://synthetic.invalid/policy"),
        SourceCase("data_source", external_source_id="reserved-source-1"),
        SourceCase("upload", valid=False),
        SourceCase(
            "web", object_key="unexpected", source_url="https://synthetic.invalid", valid=False
        ),
        SourceCase("data_source", valid=False),
    ],
)
def test_document_source_validates_each_reserved_source_shape(case: SourceCase) -> None:
    source = DocumentSource(
        source_id=UUID("42000000-0000-4000-8000-000000000101"),
        workspace_id=WORKSPACE_ID,
        document_version_id=VERSION_ID,
        source_kind=case.kind,
        source_name="合成来源",
        original_object_key=case.object_key,
        source_path=None,
        source_url=case.source_url,
        external_source_id=case.external_source_id,
        captured_at=None,
        created_at=NOW,
    )

    if case.valid:
        source.assert_valid()
    else:
        with pytest.raises(InvalidKnowledgeFactError):
            source.assert_valid()
