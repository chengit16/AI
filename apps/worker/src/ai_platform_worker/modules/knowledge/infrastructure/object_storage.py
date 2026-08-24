"""使用 MinIO 的幂等删除语义清理已永久删除文档的外部对象。"""

from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

from minio import Minio

from ai_platform_worker.modules.knowledge.domain.object_cleanup import (
    InvalidObjectCleanupEventError,
    ObjectCleanupUnavailableError,
)


class MinioObjectCleanupStorage:
    """只接收应用层已校验的对象键，并把供应商异常映射为稳定错误。"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("对象存储 endpoint 必须是无路径的 HTTP(S) 地址")
        if not access_key or not secret_key or not bucket:
            raise ValueError("对象存储凭证与 Bucket 不能为空")
        self._client = Minio(
            parsed.netloc,
            access_key=access_key,
            secret_key=secret_key,
            secure=parsed.scheme == "https",
        )
        self._bucket = bucket

    def delete(self, *, workspace_id: UUID, object_key: str) -> None:
        """删除对象；不存在对象由 S3-compatible API 按成功处理，支持安全重放。"""

        # Adapter 再次执行不可扩大的前缀校验，防止未来调用方绕过应用服务直接传键。
        if not object_key.startswith(f"workspaces/{workspace_id}/"):
            raise InvalidObjectCleanupEventError("对象清理键不属于当前工作空间")
        try:
            self._client.remove_object(self._bucket, object_key)
        except Exception as error:
            raise ObjectCleanupUnavailableError from error
