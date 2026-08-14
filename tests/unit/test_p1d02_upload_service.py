"""验证 P1D-02 上传对象写入、事务回滚和入库任务创建。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.knowledge.application.facts import (
    KnowledgeConflictError,
    KnowledgeFactService,
    KnowledgeValidationError,
)
from ai_platform_api.modules.knowledge.application.uploads import (
    KnowledgeUploadService,
    UploadSecurityUnavailableError,
)
from ai_platform_api.modules.knowledge.domain.models import (
    Document,
    DocumentSource,
    DocumentVersion,
    KnowledgeUnitOfWork,
)
from ai_platform_api.modules.knowledge.domain.uploads import (
    InspectedUpload,
    ObjectStorage,
    ObjectStorageUnavailableError,
    ScanResult,
    UploadScannerUnavailableError,
    WorkspaceObject,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000203")
KNOWLEDGE_BASE_ID = UUID("30000000-0000-4000-8000-000000000203")
DOCUMENT_ID = UUID("40000000-0000-4000-8000-000000000203")
VERSION_ID = UUID("41000000-0000-4000-8000-000000000203")
SOURCE_ID = UUID("42000000-0000-4000-8000-000000000203")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000203")
NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


def context() -> RequestContext:
    return RequestContext(
        request_id=UUID("90000000-0000-4000-8000-000000000203"),
        trace=TraceContext.new(),
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        authentication_method="browser_session",
        credential_scopes=frozenset(),
    )


@dataclass
class FakeFacts:
    deny_target: bool = False
    fail_create: bool = False
    target_checks: int = 0
    creates: int = 0

    def require_upload_target(
        self,
        request_context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID | None = None,
    ) -> None:
        assert request_context.workspace_id == WORKSPACE_ID
        assert knowledge_base_id == KNOWLEDGE_BASE_ID
        self.target_checks += 1
        if self.deny_target:
            raise KnowledgeConflictError

    def create_document(self, request_context: RequestContext, **_: object) -> tuple[object, ...]:
        assert request_context.workspace_id == WORKSPACE_ID
        self.creates += 1
        if self.fail_create:
            raise KnowledgeConflictError
        document = Document(
            DOCUMENT_ID,
            WORKSPACE_ID,
            KNOWLEDGE_BASE_ID,
            "合成文档",
            "private",
            frozenset(),
            "INTERNAL",
            frozenset(),
            "active",
            ACCOUNT_ID,
            NOW,
            NOW,
        )
        version = DocumentVersion(
            VERSION_ID,
            WORKSPACE_ID,
            DOCUMENT_ID,
            1,
            "draft",
            None,
            ACCOUNT_ID,
            NOW,
        )
        source = DocumentSource(
            SOURCE_ID,
            WORKSPACE_ID,
            VERSION_ID,
            "upload",
            "synthetic.txt",
            f"workspaces/{WORKSPACE_ID}/uploads/synthetic.txt",
            None,
            None,
            None,
            None,
            NOW,
            "text/plain",
            9,
            "a" * 64,
            "clean",
            "fake-scanner-v1",
            NOW,
        )
        return document, version, source


class FakeInspector:
    def inspect(self, **_: object) -> InspectedUpload:
        content = "合成内容".encode()
        return InspectedUpload("synthetic.txt", "text/plain", len(content), "a" * 64, content)


@dataclass
class FakeScanner:
    unavailable: bool = False
    calls: int = 0

    def scan(self, upload: InspectedUpload) -> ScanResult:
        assert upload.safe_file_name == "synthetic.txt"
        self.calls += 1
        if self.unavailable:
            raise UploadScannerUnavailableError
        return ScanResult("clean", "fake-scanner-v1")


@dataclass
class FakeStorage:
    puts: int = 0
    deletes: int = 0
    last_location: WorkspaceObject | None = None

    def put(self, location: WorkspaceObject, upload: InspectedUpload, scan: ScanResult) -> None:
        location.assert_valid()
        scan.assert_clean()
        self.puts += 1
        self.last_location = location

    def get(self, location: WorkspaceObject) -> bytes:
        if location != self.last_location:
            raise ObjectStorageUnavailableError
        return b"synthetic"

    def delete(self, location: WorkspaceObject) -> None:
        assert location == self.last_location
        self.deletes += 1


def service(
    facts: FakeFacts,
    storage: FakeStorage,
    scanner: FakeScanner,
) -> KnowledgeUploadService:
    return KnowledgeUploadService(
        cast(KnowledgeFactService, facts),
        cast(ObjectStorage, storage),
        FakeInspector(),
        scanner,
    )


def upload(service_under_test: KnowledgeUploadService) -> None:
    service_under_test.upload_document(
        context(),
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        title="合成文档",
        file_name="synthetic.txt",
        declared_media_type="text/plain",
        content=b"synthetic",
        visibility=None,
        department_ids=None,
        security_level=None,
        permission_labels=frozenset(),
    )


def test_denied_target_does_not_scan_or_write_object() -> None:
    facts = FakeFacts(deny_target=True)
    storage = FakeStorage()
    scanner = FakeScanner()

    with pytest.raises(KnowledgeConflictError):
        upload(service(facts, storage, scanner))

    assert facts.target_checks == 1
    assert scanner.calls == 0
    assert storage.puts == 0


def test_scanner_unavailable_fails_closed_without_object_or_fact() -> None:
    facts = FakeFacts()
    storage = FakeStorage()
    scanner = FakeScanner(unavailable=True)

    with pytest.raises(UploadSecurityUnavailableError):
        upload(service(facts, storage, scanner))

    assert scanner.calls == 1
    assert storage.puts == 0
    assert facts.creates == 0


def test_fact_failure_compensates_stored_object() -> None:
    facts = FakeFacts(fail_create=True)
    storage = FakeStorage()

    with pytest.raises(KnowledgeConflictError):
        upload(service(facts, storage, FakeScanner()))

    assert storage.puts == 1
    assert storage.deletes == 1


def test_fact_service_rejects_upload_without_security_fact_before_transaction() -> None:
    class ForbiddenUnitOfWork:
        def __enter__(self) -> object:
            raise AssertionError("缺少上传安全事实时不得进入事务")

    facts = KnowledgeFactService(cast(KnowledgeUnitOfWork, ForbiddenUnitOfWork()))

    with pytest.raises(KnowledgeValidationError):
        facts.create_document(
            context(),
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            title="伪造上传",
            source_kind="upload",
            source_name="forged.pdf",
            original_object_key=f"workspaces/{WORKSPACE_ID}/uploads/forged.pdf",
        )
