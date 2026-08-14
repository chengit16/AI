from __future__ import annotations

from io import BytesIO

from minio import Minio
from minio.error import S3Error

from ai_platform_worker.modules.ingestion.domain.jobs import (
    ClaimedIngestionJob,
    IngestionStorageUnavailableError,
    ParsedArtifact,
)


class MinioIngestionObjectStorage:
    """只允许读取任务快照中的工作空间来源，并写入确定性解析产物键。"""

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

    def read_source(self, job: ClaimedIngestionJob) -> bytes:
        self._assert_key(job, job.source_object_key, area="uploads")
        response = None
        try:
            response = self._client.get_object(self._bucket, job.source_object_key)
            return response.read()
        except Exception as error:
            raise IngestionStorageUnavailableError from error
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def write_artifact(self, job: ClaimedIngestionJob, artifact: ParsedArtifact) -> None:
        self._assert_key(job, artifact.object_key, area="parsed")
        try:
            self._ensure_bucket()
            self._client.put_object(
                self._bucket,
                artifact.object_key,
                BytesIO(artifact.payload),
                len(artifact.payload),
                content_type="application/json",
                metadata={
                    "ingestion-job-id": str(job.ingestion_job_id),
                    "content-sha256": artifact.content_hash,
                },
            )
        except Exception as error:
            raise IngestionStorageUnavailableError from error

    def _ensure_bucket(self) -> None:
        if self._client.bucket_exists(self._bucket):
            return
        try:
            self._client.make_bucket(self._bucket)
        except S3Error as error:
            if error.code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                raise

    @staticmethod
    def _assert_key(job: ClaimedIngestionJob, object_key: str, *, area: str) -> None:
        if not object_key.startswith(f"workspaces/{job.workspace_id}/{area}/"):
            raise IngestionStorageUnavailableError
