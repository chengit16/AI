from __future__ import annotations

import socket
from dataclasses import dataclass

from ai_platform_backend.database import PlatformDatabase
from ai_platform_backend.integration.application import OutboxDispatcher
from ai_platform_backend.integration.envelope import HmacTaskEnvelopeSigner, SigningKeyFile
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyConsumerUnitOfWork,
    SqlAlchemyOutboxLeaseStore,
)

from ai_platform_worker.config import WorkerSettings, get_worker_settings
from ai_platform_worker.consumers.publisher import CeleryTaskPublisher
from ai_platform_worker.modules.ingestion.application.ingest import ParseDocument
from ai_platform_worker.modules.ingestion.application.jobs import IngestionJobProcessor
from ai_platform_worker.modules.ingestion.domain.documents import IngestionLimits
from ai_platform_worker.modules.ingestion.infrastructure.jobs_sqlalchemy import (
    SqlAlchemyIngestionJobStore,
)
from ai_platform_worker.modules.ingestion.infrastructure.object_storage import (
    MinioIngestionObjectStorage,
)
from ai_platform_worker.modules.ingestion.infrastructure.parsers import DefaultParserRouter
from ai_platform_worker.modules.ingestion.infrastructure.pdf import PdfDocumentParser
from ai_platform_worker.modules.ingestion.infrastructure.tika import (
    TikaChineseOcrAdapter,
    TikaDocumentParser,
)


@dataclass(frozen=True)
class WorkerRuntime:
    database: PlatformDatabase
    dispatcher: OutboxDispatcher
    consumers: SqlAlchemyConsumerUnitOfWork
    ingestion: IngestionJobProcessor

    def close(self) -> None:
        self.database.close()


def build_worker_runtime(settings: WorkerSettings | None = None) -> WorkerRuntime:
    resolved = settings or get_worker_settings()
    database = PlatformDatabase.create(resolved.database_url)
    signer = HmacTaskEnvelopeSigner(SigningKeyFile(resolved.task_signing_key_path).load())
    worker_id = f"{socket.gethostname()}:{id(database)}"
    chinese_ocr = TikaChineseOcrAdapter(resolved.tika_url)
    tika_parser = TikaDocumentParser(resolved.tika_url)
    return WorkerRuntime(
        database=database,
        dispatcher=OutboxDispatcher(
            SqlAlchemyOutboxLeaseStore(database.sessions),
            CeleryTaskPublisher(signer),
            worker_id=worker_id,
            batch_size=resolved.outbox_batch_size,
            lease_seconds=resolved.outbox_lease_seconds,
            max_attempts=resolved.outbox_max_attempts,
            base_retry_seconds=resolved.outbox_retry_base_seconds,
        ),
        consumers=SqlAlchemyConsumerUnitOfWork(database.sessions),
        ingestion=IngestionJobProcessor(
            SqlAlchemyIngestionJobStore(database.sessions),
            MinioIngestionObjectStorage(
                endpoint=resolved.minio_endpoint,
                access_key=resolved.minio_access_key,
                secret_key=resolved.minio_secret_key.get_secret_value(),
                bucket=resolved.minio_bucket,
            ),
            ParseDocument(
                DefaultParserRouter(
                    tika_parser,
                    PdfDocumentParser(chinese_ocr),
                    chinese_ocr,
                )
            ),
            IngestionLimits(
                max_file_size_bytes=resolved.ingestion_max_file_size_bytes,
                max_page_count=resolved.ingestion_max_page_count,
                max_chunk_chars=1_500,
                chunk_overlap_chars=150,
            ),
            worker_id=worker_id,
            lease_seconds=resolved.ingestion_lease_seconds,
            retry_base_seconds=resolved.ingestion_retry_base_seconds,
        ),
    )
