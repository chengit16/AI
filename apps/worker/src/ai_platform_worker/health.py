import json
from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class WorkerHealth:
    service: str
    status: Literal["ok"]
    version: str


def get_worker_health() -> WorkerHealth:
    return WorkerHealth(service="ai-platform-worker", status="ok", version="0.0.0")


def main() -> None:
    print(json.dumps(asdict(get_worker_health()), ensure_ascii=False))


if __name__ == "__main__":
    main()
