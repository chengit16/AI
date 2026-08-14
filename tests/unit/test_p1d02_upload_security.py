"""验证 P1D-02 上传类型、签名、大小和安全扫描规则。"""

from __future__ import annotations

import io
import zipfile
from uuid import UUID

import pytest
from ai_platform_api.modules.knowledge.domain.uploads import (
    EmptyUploadError,
    InspectedUpload,
    InvalidUploadError,
    UnsafeUploadError,
    UploadMediaTypeMismatchError,
    UploadTooLargeError,
    WorkspaceObject,
)
from ai_platform_api.modules.knowledge.infrastructure.upload_security import (
    MEBIBYTE,
    BoundedUploadInspector,
    DeterministicUploadScanner,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000201")


def inspector() -> BoundedUploadInspector:
    return BoundedUploadInspector(MEBIBYTE)


def inspected(content: bytes, media_type: str) -> InspectedUpload:
    return InspectedUpload("synthetic.bin", media_type, len(content), "a" * 64, content)


def docx(*, macro: bool = False) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document>合成内容</document>")
        if macro:
            archive.writestr("word/vbaProject.bin", b"synthetic macro")
    return payload.getvalue()


def test_inspector_accepts_matching_text_and_fixes_hash() -> None:
    result = inspector().inspect(
        file_name="../policy.md",
        declared_media_type="text/markdown; charset=utf-8",
        content="# 合成政策\n".encode(),
    )

    assert result.safe_file_name == "policy.md"
    assert result.media_type == "text/markdown"
    assert result.size_bytes == len(result.content)
    assert len(result.content_hash) == 64


def test_inspector_rejects_empty_oversized_and_forged_media_type() -> None:
    with pytest.raises(EmptyUploadError):
        inspector().inspect(file_name="empty.txt", declared_media_type="text/plain", content=b"")
    with pytest.raises(UploadTooLargeError):
        inspector().inspect(
            file_name="large.txt",
            declared_media_type="text/plain",
            content=b"a" * (MEBIBYTE + 1),
        )
    with pytest.raises(UploadMediaTypeMismatchError):
        inspector().inspect(
            file_name="forged.pdf",
            declared_media_type="application/pdf",
            content=b"plain text",
        )


def test_scanner_rejects_eicar_active_pdf_and_docx_macro() -> None:
    scanner = DeterministicUploadScanner()
    cases = (
        inspected(
            b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
            "text/plain",
        ),
        inspected(b"%PDF-1.7\n/JavaScript", "application/pdf"),
        inspected(
            docx(macro=True),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    )

    for case in cases:
        with pytest.raises(UnsafeUploadError):
            scanner.scan(case)


def test_scanner_accepts_minimal_docx_and_workspace_key_isolated() -> None:
    result = DeterministicUploadScanner().scan(
        inspected(
            docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    )
    result.assert_clean()

    WorkspaceObject(
        WORKSPACE_ID,
        f"workspaces/{WORKSPACE_ID}/uploads/synthetic.docx",
    ).assert_valid()
    with pytest.raises(InvalidUploadError):
        WorkspaceObject(WORKSPACE_ID, "workspaces/other/uploads/synthetic.docx").assert_valid()
