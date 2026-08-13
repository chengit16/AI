# Python 后端共享内核

本目录只承载 API 与 Worker 已经发生真实复用的后端能力。当前范围包括版本化集成事件、审计事实、Transactional Outbox 调度接口、签名任务信封和对应 PostgreSQL 表定义。

共享内核不得依赖 FastAPI、Celery 应用入口或任一进程的配置模块；API 和 Worker 分别在自己的装配层选择 Adapter。跨语言协议的事实来源仍是仓库根目录 `contracts/`，本目录不是第二套契约。
