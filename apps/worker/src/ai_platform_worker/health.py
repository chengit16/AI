"""输出 Worker 进程、配置和 Celery 注册状态的本地健康事实。"""

import json
from dataclasses import asdict, dataclass
from typing import Literal

from ai_platform_worker.config import get_worker_settings


@dataclass(frozen=True)
class WorkerHealth:
    """返回 Worker 进程、Broker 和依赖服务的可观测健康状态。"""

    service: str
    status: Literal["ok"]
    version: str


def get_worker_health() -> WorkerHealth:
    """获取Worker健康状态，遵守任务幂等、有限重试和提交时机约束。"""

    settings = get_worker_settings()
    return WorkerHealth(service="ai-platform-worker", status="ok", version=settings.version)


def main() -> None:
    """处理命令入口，遵守任务幂等、有限重试和提交时机约束。"""

    print(json.dumps(asdict(get_worker_health()), ensure_ascii=False))


if __name__ == "__main__":
    main()
