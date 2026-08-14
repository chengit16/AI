"""从工作空间隔离对象键读取索引输入和写入确定性构建产物。"""

from ai_platform_backend.indexing.domain import ClaimedIndexVersion
from minio import Minio

from ai_platform_worker.modules.indexing.domain.errors import IndexStorageUnavailableError


class MinioIndexArtifactStorage:
    """只允许读取索引版本已冻结的工作空间解析产物。"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        secure = endpoint.startswith("https://")
        normalized = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._client = Minio(
            normalized,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket = bucket

    def read_artifact(self, version: ClaimedIndexVersion) -> bytes:
        if not version.artifact_object_key.startswith(f"workspaces/{version.workspace_id}/parsed/"):
            raise IndexStorageUnavailableError
        response = None
        try:
            response = self._client.get_object(self._bucket, version.artifact_object_key)
            return response.read()
        except Exception as error:
            raise IndexStorageUnavailableError from error
        finally:
            if response is not None:
                response.close()
                response.release_conn()
