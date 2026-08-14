"""以工作空间前缀和确定性对象键隔离 MinIO 文档内容。"""

from __future__ import annotations

import io
from threading import Lock
from urllib.parse import urlsplit

from minio import Minio

from ai_platform_api.modules.knowledge.domain.uploads import (
    InspectedUpload,
    ObjectStorageUnavailableError,
    ScanResult,
    WorkspaceObject,
)


class MinioObjectStorage:
    """MinIO 只是当前 S3-compatible Adapter，业务层不依赖其对象或异常类型。"""

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
        self._bucket_ready = False
        self._bucket_lock = Lock()

    def put(self, location: WorkspaceObject, upload: InspectedUpload, scan: ScanResult) -> None:
        location.assert_valid()
        scan.assert_clean()
        try:
            self._ensure_bucket()
            self._client.put_object(
                self._bucket,
                location.object_key,
                io.BytesIO(upload.content),
                upload.size_bytes,
                content_type=upload.media_type,
                metadata={
                    "content-sha256": upload.content_hash,
                    "scanner-version": scan.scanner_version,
                    "scan-status": scan.status,
                },
            )
        except Exception as error:
            raise ObjectStorageUnavailableError from error

    def get(self, location: WorkspaceObject) -> bytes:
        location.assert_valid()
        response = None
        try:
            response = self._client.get_object(self._bucket, location.object_key)
            return response.read()
        except Exception as error:
            raise ObjectStorageUnavailableError from error
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def delete(self, location: WorkspaceObject) -> None:
        location.assert_valid()
        try:
            self._client.remove_object(self._bucket, location.object_key)
        except Exception as error:
            raise ObjectStorageUnavailableError from error

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        # 首批并发上传只能由一个线程执行 exists/create，避免 Bucket 初始化竞态。
        with self._bucket_lock:
            if self._bucket_ready:
                return
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
            self._bucket_ready = True
