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


@dataclass(frozen=True)
class WorkerRuntime:
    database: PlatformDatabase
    dispatcher: OutboxDispatcher
    consumers: SqlAlchemyConsumerUnitOfWork

    def close(self) -> None:
        self.database.close()


def build_worker_runtime(settings: WorkerSettings | None = None) -> WorkerRuntime:
    resolved = settings or get_worker_settings()
    database = PlatformDatabase.create(resolved.database_url)
    signer = HmacTaskEnvelopeSigner(SigningKeyFile(resolved.task_signing_key_path).load())
    return WorkerRuntime(
        database=database,
        dispatcher=OutboxDispatcher(
            SqlAlchemyOutboxLeaseStore(database.sessions),
            CeleryTaskPublisher(signer),
            worker_id=f"{socket.gethostname()}:{id(database)}",
            batch_size=resolved.outbox_batch_size,
            lease_seconds=resolved.outbox_lease_seconds,
            max_attempts=resolved.outbox_max_attempts,
            base_retry_seconds=resolved.outbox_retry_base_seconds,
        ),
        consumers=SqlAlchemyConsumerUnitOfWork(database.sessions),
    )
