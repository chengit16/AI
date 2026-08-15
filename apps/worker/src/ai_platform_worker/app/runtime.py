"""按任务边界惰性装配数据库、对象存储、解析和索引运行依赖。"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from ai_platform_backend.database import PlatformDatabase
from ai_platform_backend.indexing.tokenization import TOKENIZER_VERSION
from ai_platform_backend.integration.application import OutboxDispatcher
from ai_platform_backend.integration.envelope import HmacTaskEnvelopeSigner, SigningKeyFile
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyConsumerUnitOfWork,
    SqlAlchemyOutboxLeaseStore,
)

from ai_platform_worker.config import WorkerSettings, get_worker_settings
from ai_platform_worker.consumers.publisher import CeleryTaskPublisher
from ai_platform_worker.modules.indexing.application.build import (
    IndexCommitProcessor,
    IndexEmbeddingProcessor,
)
from ai_platform_worker.modules.indexing.application.maintenance import (
    IndexMaintenanceProcessor,
)
from ai_platform_worker.modules.indexing.infrastructure.embeddings import (
    DeterministicHashEmbeddingAdapter,
)
from ai_platform_worker.modules.indexing.infrastructure.maintenance_sqlalchemy import (
    SqlAlchemyIndexMaintenanceStore,
)
from ai_platform_worker.modules.indexing.infrastructure.object_storage import (
    MinioIndexArtifactStorage,
)
from ai_platform_worker.modules.indexing.infrastructure.sqlalchemy import (
    SqlAlchemyIndexVersionStore,
)
from ai_platform_worker.modules.ingestion.application.chunking import StructuralChunker
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
    """集中装配 Worker 数据库、对象存储、解析器、消费者和调度生命周期。"""

    database: PlatformDatabase
    dispatcher: OutboxDispatcher
    consumers: SqlAlchemyConsumerUnitOfWork
    ingestion: IngestionJobProcessor
    embedding: IndexEmbeddingProcessor
    indexing: IndexCommitProcessor
    index_maintenance: IndexMaintenanceProcessor

    def close(self) -> None:
        self.database.close()


def build_worker_runtime(settings: WorkerSettings | None = None) -> WorkerRuntime:
    """构建Worker运行时，遵守任务幂等、有限重试和提交时机约束。"""

    # 1. 先装配所有 Lane 共用的数据库、签名和解析 Adapter，避免任务间隐式全局连接。
    resolved = settings or get_worker_settings()
    database = PlatformDatabase.create(resolved.database_url)
    signer = HmacTaskEnvelopeSigner(SigningKeyFile(resolved.task_signing_key_path).load())
    worker_id = f"{socket.gethostname()}:{id(database)}"
    chinese_ocr = TikaChineseOcrAdapter(resolved.tika_url)
    tika_parser = TikaDocumentParser(resolved.tika_url)
    embedding_adapter = DeterministicHashEmbeddingAdapter()
    # 2. 控制面和入库 Lane 各自取得短事务 Store，外部解析始终在事务之外执行。
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
        # 3. Embedding 与索引提交使用独立处理器，Compose 再将二者固定到不同进程。
        embedding=IndexEmbeddingProcessor(
            SqlAlchemyIndexVersionStore(database.sessions),
            MinioIndexArtifactStorage(
                endpoint=resolved.minio_endpoint,
                access_key=resolved.minio_access_key,
                secret_key=resolved.minio_secret_key.get_secret_value(),
                bucket=resolved.minio_bucket,
            ),
            StructuralChunker(),
            embedding_adapter,
            IngestionLimits(
                max_file_size_bytes=resolved.ingestion_max_file_size_bytes,
                max_page_count=resolved.ingestion_max_page_count,
                max_chunk_chars=resolved.indexing_max_chunk_chars,
                chunk_overlap_chars=resolved.indexing_chunk_overlap_chars,
            ),
            worker_id=worker_id,
            lease_seconds=resolved.indexing_lease_seconds,
            retry_base_seconds=resolved.indexing_retry_base_seconds,
            max_attempts=resolved.indexing_max_attempts,
            chunker_version=resolved.indexing_chunker_version,
            tokenizer_version=TOKENIZER_VERSION,
        ),
        indexing=IndexCommitProcessor(
            SqlAlchemyIndexVersionStore(database.sessions),
            worker_id=worker_id,
            lease_seconds=resolved.indexing_lease_seconds,
            retry_base_seconds=resolved.indexing_retry_base_seconds,
        ),
        index_maintenance=IndexMaintenanceProcessor(
            SqlAlchemyIndexMaintenanceStore(database.sessions),
            max_attempts=resolved.indexing_max_attempts,
            chunker_version=resolved.indexing_chunker_version,
            embedding_model_version=embedding_adapter.model_version,
            tokenizer_version=TOKENIZER_VERSION,
        ),
    )
