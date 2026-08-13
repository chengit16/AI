import json
from dataclasses import asdict, dataclass
from typing import Literal

from ai_platform_worker.config import get_worker_settings


@dataclass(frozen=True)
class WorkerHealth:
    service: str
    status: Literal["ok"]
    version: str


def get_worker_health() -> WorkerHealth:
    settings = get_worker_settings()
    return WorkerHealth(service="ai-platform-worker", status="ok", version=settings.version)


def main() -> None:
    print(json.dumps(asdict(get_worker_health()), ensure_ascii=False))


if __name__ == "__main__":
    main()
