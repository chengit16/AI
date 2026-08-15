"""通过 S3-compatible Adapter 导出和清除工作空间对象。"""

from __future__ import annotations

import hashlib
import io
from urllib.parse import urlsplit
from uuid import UUID

from minio import Minio

from ai_platform_api.modules.lifecycle.domain.models import ExportObject
from ai_platform_api.modules.lifecycle.domain.ports import LifecycleDependencyError


class MinioLifecycleObjectStorage:
    """只允许在目标工作空间前缀内执行导出和幂等删除。"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("生命周期对象存储 endpoint 无效")
        self._client = Minio(
            parsed.netloc,
            access_key=access_key,
            secret_key=secret_key,
            secure=parsed.scheme == "https",
        )
        self._bucket = bucket

    def read_business_objects(self, workspace_id: UUID) -> tuple[ExportObject, ...]:
        """读取除导出包外的空间对象，并在返回前复算内容摘要。"""

        prefix = self._prefix(workspace_id)
        try:
            if not self._client.bucket_exists(self._bucket):
                return ()
            result: list[ExportObject] = []
            for item in self._client.list_objects(self._bucket, prefix=prefix, recursive=True):
                if item.object_name.startswith(f"{prefix}exports/"):
                    continue
                response = self._client.get_object(self._bucket, item.object_name)
                try:
                    content = response.read()
                finally:
                    response.close()
                    response.release_conn()
                result.append(
                    ExportObject(
                        object_key=item.object_name,
                        content=content,
                        sha256=hashlib.sha256(content).hexdigest(),
                    )
                )
            return tuple(sorted(result, key=lambda value: value.object_key))
        except Exception as error:
            raise LifecycleDependencyError from error

    def put_export(self, workspace_id: UUID, export_id: UUID, content: bytes, sha256: str) -> str:
        """写入服务端生成的导出包，并保存摘要元数据。"""

        object_key = f"{self._prefix(workspace_id)}exports/{export_id}.zip"
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
            self._client.put_object(
                self._bucket,
                object_key,
                io.BytesIO(content),
                len(content),
                content_type="application/zip",
                metadata={"content-sha256": sha256, "export-format": "workspace-export.v1"},
            )
            return object_key
        except Exception as error:
            raise LifecycleDependencyError from error

    def delete_workspace_objects(self, workspace_id: UUID) -> int:
        """幂等删除目标工作空间完整前缀，并返回本次实际发现的对象数。"""

        prefix = self._prefix(workspace_id)
        try:
            if not self._client.bucket_exists(self._bucket):
                return 0
            names = tuple(
                item.object_name
                for item in self._client.list_objects(self._bucket, prefix=prefix, recursive=True)
            )
            for name in names:
                if not name.startswith(prefix):
                    raise LifecycleDependencyError("对象存储返回了越界对象键")
                self._client.remove_object(self._bucket, name)
            return len(names)
        except LifecycleDependencyError:
            raise
        except Exception as error:
            raise LifecycleDependencyError from error

    @staticmethod
    def _prefix(workspace_id: UUID) -> str:
        return f"workspaces/{workspace_id}/"
