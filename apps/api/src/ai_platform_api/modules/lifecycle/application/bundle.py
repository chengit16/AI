"""构建可重复校验的工作空间级 ZIP 导出包。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from ai_platform_api.modules.lifecycle.domain.models import ExportObject


@dataclass(frozen=True)
class WorkspaceExportBundle:
    """返回导出包字节及外部完成事实需要的摘要。"""

    content: bytes
    sha256: str
    object_manifest_sha256: str
    table_count: int
    object_count: int


def build_workspace_export_bundle(
    *,
    workspace_id: UUID,
    export_id: UUID,
    created_at: datetime,
    registry_version: int,
    table_rows: dict[str, tuple[dict[str, object], ...]],
    objects: tuple[ExportObject, ...],
) -> WorkspaceExportBundle:
    """按稳定文件名、排序和时间戳生成 `workspace-export.v1` 包。"""

    # 1. 先规范化表文件和对象清单，Manifest 中的每项都能脱离数据库独立复算。
    table_files = {
        f"tables/{table}.jsonl": _jsonl(rows) for table, rows in sorted(table_rows.items())
    }
    object_files = {
        f"objects/{item.object_key}": item.content
        for item in sorted(objects, key=lambda value: value.object_key)
    }
    object_manifest = [
        {
            "object_key": item.object_key,
            "size_bytes": len(item.content),
            "sha256": item.sha256,
        }
        for item in sorted(objects, key=lambda value: value.object_key)
    ]
    object_manifest_bytes = _canonical_json(object_manifest)
    manifest = {
        "format": "workspace-export.v1",
        "schema_version": 1,
        "registry_version": registry_version,
        "workspace_id": str(workspace_id),
        "export_id": str(export_id),
        "created_at": created_at.isoformat(),
        "tables": [
            {
                "table": path.removeprefix("tables/").removesuffix(".jsonl"),
                "path": path,
                "row_count": len(table_rows[path.removeprefix("tables/").removesuffix(".jsonl")]),
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in sorted(table_files.items())
        ],
        "objects": object_manifest,
        "object_manifest_sha256": hashlib.sha256(object_manifest_bytes).hexdigest(),
    }
    files = {
        "manifest.json": _canonical_json(manifest),
        **table_files,
        **object_files,
    }
    # 2. ZIP 元数据固定到最早合法时间与权限位，避免运行机器和生成时刻改变包摘要。
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path, content in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    content = output.getvalue()
    return WorkspaceExportBundle(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        object_manifest_sha256=hashlib.sha256(object_manifest_bytes).hexdigest(),
        table_count=len(table_rows),
        object_count=len(objects),
    )


def _jsonl(rows: tuple[dict[str, object], ...]) -> bytes:
    if not rows:
        return b""
    return b"\n".join(_canonical_json(row) for row in rows) + b"\n"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
