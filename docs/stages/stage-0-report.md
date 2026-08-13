# 阶段 0 技术验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 0：需求基线与技术验证 |
| 状态 | 已完成，标签 `stage-0-complete` |
| 报告日期 | 2026-08-13 |
| `core_functional` | `passed`，仅代表阶段 0 技术验证范围 |
| `provider_integration` | `not_configured` |
| `ai_quality` | `not_configured` |
| `capacity_certification` | `not_run` |

## 2. 验证环境

| 项目 | 实际值 |
| --- | --- |
| 操作系统 | macOS 26.5.2，Apple Silicon `arm64` |
| CPU / 内存 | Apple M3 Pro 11 核 / 18 GB |
| Node.js | 24.19.0 |
| pnpm | 11.20.0 |
| uv | 0.10.7 |
| 项目 Python | 3.12.12，由 uv 管理 |
| Docker Desktop | 4.86.0 |
| Docker Engine | 29.7.2 |
| Docker Compose | v5.3.1 |

## 3. 节点验证记录

### P0-01 交付与跟踪基线

- 日期：2026-08-13。
- 结果：通过。
- 验证内容：文档存在性、内部路径、节点编号和 `git diff --check`。
- 提交：`0008a25`。

### P0-02 工程骨架

- 状态：通过。
- 已确认 Node.js 24.19.0、pnpm 11.20.0、uv 0.10.7 和项目 Python 3.12.12。
- 已建立 Monorepo 目录职责、Node/Python 版本约束、工作区配置、忽略规则和非敏感 `.env.example`。
- `pnpm install --offline --frozen-lockfile`、`uv run --locked ruff check .` 和 `uv run --locked mypy .` 检查通过。
- `git check-ignore` 已确认本地环境变量、密钥、虚拟环境、项目 Python 和验证产物不会进入仓库。
- 提交：`f7cecab`。

### P0-03 最小应用健康链路

- 状态：通过。
- Web：React 19、TypeScript、Vite、React Router、TanStack Query、Zustand 和 Ant Design 运行中心。
- API：FastAPI 存活与就绪端点，OpenAPI 3.1 文档入口。
- Worker：独立健康状态模型和命令入口。
- 自动化验收：Vitest 1 项、pytest 3 项通过；ESLint、Ruff、mypy、TypeScript 和生产构建通过。
- 浏览器验收：1440×900 与 390×844 视口无横向溢出；API 状态和刷新操作正常；最终控制台无错误或警告。
- 已知限制：当前 Web 初始生产资源约 556 KB，其中 Ant Design vendor 约 474 KB；阶段 0 仅有一个轻量页面，不阻塞节点，后续按菜单路由实施页面级懒加载。
- 提交：`0a52b02`。

### P0-04 核心契约 V1

- 状态：通过。
- 固定内容：OpenAPI 3.1、核心身份、`AgentRelease`、`MessagePart`、SSE `MessageEvent`、Transactional Outbox 集成事件、策略请求/结果、错误码目录，以及 `DataSource`、`RelevanceGrader`、`MultimodalModelRouter` 扩展点。
- 兼容原则：同一主版本只允许兼容性新增；关键标识不得改名；SSE 与集成事件允许旧消费者忽略未知可选字段。
- Golden Fixtures：全部使用明确的合成 UUID 和合成文本，可供当前 Python 与未来 Go 实现复用。
- 自动化验收：9 项契约测试通过；有效样例、错误码唯一性、OpenAPI 实现一致性，以及缺失 `workspace_id`、非法策略决策、无效 `sequence_no` 三类反例均已覆盖。项目全部 pytest 共 12 项通过。
- 已知限制：本节点只冻结接口和数据契约，不实现策略引擎、Outbox 发布器、SSE 存储或后置 AI 能力。
- 提交：`2cc739b`。

### P0-05 本地基础设施与应用容器编排

- 状态：通过。
- 固定镜像与运行时：PostgreSQL 16 + pgvector、Redis 7.4、MinIO `RELEASE.2025-07-23T15-54-02Z`、Apache Tika `3.2.3.0-full`、Python `3.12.12-slim-bookworm`、Node.js `24.19.0-alpine` 和 Nginx `1.29.1-alpine`。
- 编排结果：PostgreSQL、Redis、MinIO、Tika、API、Worker 和 Web 共七个容器全部达到 `healthy`，所有主机端口默认仅绑定 `127.0.0.1`。
- 一键运行：`./platform start`、`status`、`doctor`、`logs`、`restart` 和 `stop` 已提供；首次启动、完整停止及二次启动均通过，`doctor` 对七项服务检查全部通过。
- 依赖就绪：容器环境中的 API 就绪端点会检查 PostgreSQL、Redis、MinIO 和 Tika；任一依赖不可用时返回 HTTP 503 和 `degraded` 状态，单元测试覆盖该降级行为。
- 持久化验收：在 PostgreSQL 写入合成标记 `p0-05-persistence`，执行 `./platform stop` 后确认 `.ai-platform/data/postgres`、`redis` 和 `objects` 目录保留，二次启动后标记仍可读取。
- 页面验收：容器化 Web 在 1440×900 和 390×844 视口均显示六项 API 检查正常，无横向溢出，浏览器控制台无错误或警告。
- 自动化验收：ESLint、TypeScript、Vitest、前端生产构建、Ruff、mypy、13 项 pytest、`docker compose config --quiet` 和 `git diff --check` 全部通过。
- 安全与数据：`.env`、`.ai-platform/` 和 `AIPlatformBackups/` 均被 Git 忽略；验收仅使用合成标记，不包含真实个人、企业或供应商数据。
- 已知限制：当前凭证为本地开发默认值，只允许本机开发使用；未执行 Linux 宿主机兼容验收、真实模型供应商验收和容量认证。
- 提交：`79cf8c8`。

### P0-06 工作空间隔离、策略、Trace 与 Outbox 基线

- 状态：通过。
- 数据库：引入 SQLAlchemy 2、psycopg 3 和 Alembic；使用随机临时 PostgreSQL Schema 验证从空库升级，测试结束后清理，不修改既有本地业务数据。
- 隔离与策略：Repository 在同一 SQL 中强制 `workspace_id + resource_id`；跨空间资源统一返回不存在；策略未显式授权、资源超出范围、空间不匹配或策略服务不可用时默认拒绝；`resource_ids` 和 `field_mask` 在数据责任模块执行。
- 事务与事件：业务资源和 Outbox 事件使用同一 Session 与事务；重复 `event_id` 触发数据库约束时业务写入整体回滚；事件记录 `trace_id` 和 W3C `traceparent`。
- 幂等消费：消费者 Claim、投影更新和 Receipt 在同一事务提交；同一 `event_id` 重复处理时第二次不产生副作用，投影 `apply_count` 保持为 1。
- Trace：HTTP 边缘只接受有效 W3C `traceparent` 和 UUID 请求 ID；合法父 Trace 延续相同 Trace ID 并生成新 Span，非法输入替换为可信值，响应返回 `traceparent` 与 `x-request-id`。
- 模块边界：工作空间模块仅依赖 Outbox 领域端口，由装配处注入基础设施实现；架构门禁新增“禁止依赖其他业务模块私有 Infrastructure”反例。
- 自动化验收：PostgreSQL 集成测试 `7/7`、全量 pytest `31/31`、Web Vitest `1/1`、Node 架构测试 `3/3`；`./scripts/verify`、Ruff、mypy strict、契约兼容、生产构建和 Compose 配置全部通过。
- 容器验收：API 与 Worker 镜像使用 `uv sync --frozen` 成功重建，SQLAlchemy、psycopg 和 Alembic 依赖在 Linux/ARM64 镜像内可复现。
- 安全与数据：全部 UUID、标题和敏感字段均为合成测试数据；未接入真实个人或企业资料。
- 当前边界：本节点建立可靠性与授权最小基线，不实现正式账号认证、完整策略管理后台、Outbox 发布调度、乱序事件处理和跨工作空间复制流程，这些按后续阶段建设。
- 提交：`6a13920`。

### P0-07 文档解析、OCR 与切片链路

- 状态：通过。
- 数据集：`tests/fixtures/ingestion` 的 `p0-07-v1` 包含 7 个全合成样本：TXT、Markdown、文本 PDF、带线框表格 PDF、DOCX 表格、PNG OCR 和扫描 PDF；生成器只使用 Python 标准库，`manifest.json` 固定每个文件的 SHA-256 与字节数。
- 解析边界：TXT/Markdown 使用本地确定性解析；PDF、DOCX 和图片使用 Apache Tika `3.2.3` 结构化 HTML；PDF 由 pdfplumber `0.11.8` 补充基础线框表格。Tika 输出的非法空字符引用在 Adapter 边缘兼容处理，正文仍使用 XML 结构化解析。
- OCR：Tika Full 镜像中的 Tesseract `5.5.0` 对 PNG 精确识别 `TEST POLICY`，扫描 PDF 标记 `pdf:ocrPageCount=1`、生成非空 OCR 块并保留第 1 页来源。当前语言包没有中文，本节点不把英文 OCR 结果描述为中文 OCR 已通过；阶段 1D 必须接入 PaddleOCR 或等效中文 Adapter 后重新验收。
- 表格：Markdown、DOCX 与 PDF 三类基础表格均恢复为 `table` 结构块；PDF 表格保留页码，DOCX 表格保留行列文本。复杂合并单元格、公式、XLSX 和 PPTX 不在 MVP 范围。
- Chunk：结构感知切片优先保持页、段落和表格边界，超长块才使用字符窗口；稳定 `chunk_id` 基于文档版本、序号和内容哈希生成，重复执行结果一致；每个 Chunk 携带工作空间、知识库、文档版本、索引版本、部门、可见性、密级、来源位置、内容哈希、解析器和 OCR 标记。
- 失败定位：大小、页数、Chunk 长度和重叠均由 `IngestionLimits` 注入；空文件、超限、格式不支持、解析器不可用、解析失败和空内容使用共享 `INGESTION_*` 稳定错误码，并区分是否可重试。
- 实测结果：7 个样本全部解析成功并各生成 1 个 Chunk；本地 TXT/Markdown 为 0.5–2.0 ms，文本/表格 PDF 与 DOCX 为 7.2–9.9 ms，PNG OCR 为 146.2 ms，扫描 PDF OCR 为 247.5 ms。该结果只证明当前小样本链路正确，不构成大文件或容量认证。
- 自动化验收：真实 Tika/PDF 集成测试 `8/8`、全量 pytest `46/46`、Web Vitest `1/1`、Node 架构测试 `3/3`；`./scripts/verify`、Ruff、mypy strict、契约兼容、生产构建和 Compose 配置全部通过。
- 容器验收：Worker 镜像使用 `uv sync --frozen` 成功重建，容器内可导入 pdfplumber `0.11.8` 和 `PdfDocumentParser`，Linux/ARM64 依赖可复现。
- 安全与数据：样本内容、UUID、制度、人员和密级信息均为合成数据；未发送到外部服务，Tika 与 OCR 仅在本机容器运行。
- 当前边界：本节点验证解析、OCR 和 Chunk 产物，不实现对象存储、正式 `IngestionJob` 状态持久化、队列重试、Embedding、关键词索引和发布切换；这些由阶段 1D 及后续节点完成。
- 提交：`d89f8cc`。

### P0-08 混合检索、重排、全文精读与引用链路

- 状态：通过。
- 索引基线：PostgreSQL `vector` Extension 版本为 `0.8.6`；`retrieval_chunks` 固定 1024 维 cosine 向量、`simple` 配置的 `tsvector` 关键词列、GIN 关键词索引和 HNSW 向量索引。中文关键词使用版本化 `cjk-bigram-v1` 单字与双字确定性分词，不依赖当前数据库没有安装的 `zhparser`。
- 授权边界：所有关键词查询、向量查询、相邻 Chunk 精读和引用读取都接收 `AuthorizedSearchScope`，在 SQL 阶段同时过滤工作空间、活动索引版本、知识库、文档、部门、可见性、密级和启用状态；`content` 字段被 ABAC 掩码时直接拒绝。私有文档必须具有策略明确下发的文档集合，不能只凭同一工作空间放行。
- 排序流程：每个通道候选数、RRF 常量、重排候选数和最终证据数均由 `RetrievalBudget` 设上限；RRF 按 Chunk 去重并稳定排序；只有关键词命中且向量分数达到阈值时进入 FastPass，其余候选交给可替换 `Reranker`。Embedding 维度、输出数量或 Reranker 分数数量不符合契约时默认失败，不返回不可信排序。
- 全文精读与引用：精读只能读取同一授权文档版本的有界相邻 Chunk，按距中心证据的远近消耗 Chunk 和字符预算并保证中心证据存在；引用必须重新验证 Chunk 当前可见、索引版本有效、原文包含声明片段，并返回文档版本、索引版本、内容哈希和来源位置。
- 真实模型：离线固定 `BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181`（MIT，1024 维）和 `BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`（Apache-2.0）。模型缓存约 4.3 GB，位于被 Git 忽略的 `.ai-platform/models/huggingface`，未上传任何数据或模型产物。
- 模型实测：在 Apple M3 Pro、Torch `2.13.0` CPU 环境中，BGE-M3 对 1 条查询与 3 条合成证据加载约 1.35～2.31 秒、推理约 2.40～2.46 秒，正确证据 cosine 相似度 `0.858126` 并为 Top 1；Reranker 加载约 1.03～1.04 秒、推理约 2.36～2.37 秒，正确证据概率 `0.995468` 并为 Top 1。MPS 在当前 Torch 环境不可用，这些数据只证明小样本功能正确，不构成并发或容量认证。
- 自动化验收：领域单元测试 `13/13`，真实 PostgreSQL 集成测试 `5/5`，全量 pytest `64/64`；覆盖 RRF 去重、FastPass、重排契约、字段掩码、精读预算、引用伪造、跨空间、知识库、文档、部门、密级、旧索引版本和停用 Chunk。`./scripts/verify`、前端生产构建、契约兼容、Ruff、mypy strict 和架构检查全部通过；新增 `RETRIEVAL_SCOPE_DENIED`、`RETRIEVAL_CONFIGURATION_ERROR` 和 `CITATION_INVALID` 稳定错误码。
- 运行边界：Torch、Transformers 和本地模型只属于 `ai-validation` 依赖组和显式验证脚本，不进入默认门禁。API/Worker 镜像使用冻结锁文件重建成功，镜像内确认 `pgvector` 可用且不存在 Torch/Transformers。
- 当前边界：本节点建立可实施的检索与引用技术基线，不实现正式知识库发布任务、生产 Embedding Worker、在线模型下载、查询改写、来源权威性评分、LLM Grading 或百万 Chunk 容量认证；这些分别按阶段 1D/1E、后置能力和条件容量认证建设。
- 提交：`a02c1ae`。

### P0-09 Mock 模型网关、主备路由、降级与计量

- 状态：通过。
- 统一边界：新增 `model_gateway` 深模块，业务模块只能依赖 `ModelGateway` 的统一请求/结果接口，不能直接调用供应商 SDK、读取 API Key 或自行决定备用模型。Provider 只通过窄 `ModelProvider` Protocol 注入，Mock Adapter 不联网。
- 路由规则：路由声明模型类型、外部/私有位置和 generation、streaming、tools、structured output 能力；请求先按能力和数据外发边界过滤，禁止外发时只允许 `private` 路由。没有候选路由返回 `MODEL_ROUTE_UNAVAILABLE` 或 `MODEL_DATA_BOUNDARY_DENIED`，不会以降级文本掩盖授权失败。
- 故障策略：超时、限流和服务暂不可用允许按固定上限重试；达到连续失败阈值后打开短期熔断并尝试备用路由；认证、内容策略、非法请求和能力不支持不会盲目切备用。所有路由失败时可配置确定性规则降级，否则返回 `MODEL_GATEWAY_UNAVAILABLE`。
- 预算与响应：策略固定单次/总超时、每路由最大尝试次数、Prompt/输出/响应字符上限；响应为空、超长或 Usage 超出声明上限时失败关闭，不能把不可信结果交给上层。价格使用整数微单位估算，避免浮点误差。
- 可观测性：每次尝试记录调用 ID、路由、Provider、模型、Trace ID/Traceparent、尝试序号、状态、耗时、失败分类、Provider Request ID、Token Usage 和估算成本；Provider 原始响应和密钥不进入日志或错误对象。
- 自动化验收：Mock 故障矩阵单元测试 `9/9`；覆盖成功计量、超时有限重试后备援、内容策略禁止旁路、输入预算、私有数据边界、缺少 Provider、能力不匹配、熔断和恢复探测。契约新增 `MODEL_REQUEST_REJECTED`、`MODEL_DATA_BOUNDARY_DENIED`、`MODEL_ROUTE_UNAVAILABLE` 和 `MODEL_GATEWAY_UNAVAILABLE`。
- 当前边界：本节点不接入 GPT 中转自定义 URL/Key、通义千问、DeepSeek 或其他真实供应商，不实现在线能力探测、密钥加密存储、供应商管理菜单、流式 Provider Adapter 和真实成本结算；这些在阶段 1 模型配置与真实供应商联调时建设。
- 提交：`b63f96f`。

### P0-10 SSE 事件持久化、断点回放与并发控制

- 状态：通过。
- 事实模型：新增 `stream_runs` 和 `stream_events`。`stream_runs` 保存工作空间、会话、消息、Run 状态、最后序号、过期时间和最终快照；`stream_events` 保存事件 ID、事件类型、会话/消息/Run、严格递增序号、发生时间、过期时间、Trace、Traceparent 和 JSON Payload。SSE 连接只传输已提交事实，不把内存增量当作恢复依据。
- 并发与顺序：`stream_runs` 对同一 `conversation_id` 的 `active` 状态建立 PostgreSQL 部分唯一索引；追加事件时对 Run 行加锁后递增序号，避免并发 `max(sequence_no)+1` 竞态。同一 `event_id` 重试返回原事件，不推进序号；已结束 Run 禁止继续写入。
- 回放流程：客户端提供 `Last-Event-ID` 时只回放该事件之后的同一 Run 事件；事件 ID 不存在、工作空间不匹配、Run 不存在或游标已过期时默认失败，不猜测起点。没有未确认事件但存在最终消息快照时返回 `snapshot_required`，重连不会创建新 Run 或重复启动模型生成。
- 默认预算：事件保留 `24 小时`，单次最多回放 `5,000` 条或 `10 MB`，均由 `StreamPolicy` 配置并在 SQL 查询后和应用层字节估算双重限制；超限返回 `SSE_REPLAY_LIMIT_EXCEEDED`，过期返回 `SSE_EVENT_EXPIRED`。
- 自动化验收：领域单元测试 `7/7`，真实 PostgreSQL 集成测试 `6/6`；覆盖严格序号、Last-Event-ID、最终快照、同会话活动 Run 唯一约束、结束后新 Run、事件幂等、跨工作空间游标、未知游标、事件过期和事件/字节回放上限。
- 当前边界：本节点不接入真实 HTTP SSE Router、Redis 唤醒、跨实例连接路由、心跳调度、事件清理 Worker、完整 Message/Conversation 业务表和容量认证；这些在阶段 1/2 的会话运行层和可靠性建设中接入，当前 PostgreSQL Store 已为接口预留。
- 提交：`fe67659`。

### P0-11 合成安全评估、越权与恢复 Fixture

- 状态：通过。
- 数据集：新增 `tests/fixtures/security/p0-11-v1.json` 和中文使用说明，全部使用合成工作空间、文档、Chunk、事件、策略版本和敏感标记，不包含真实个人资料、企业资料、API Key 或供应商密钥。
- 覆盖范围：直接/间接 Prompt Injection、跨工作空间召回、撤权/删除/旧版本/旧索引引用、字段级泄漏、引用 ID 与 quote 伪造、Unicode/Base64/分隔符混淆、查询改写与全文精读越权、SSE 重复生成与回放预算、模型输出授权越权共十类样本；引用伪造和混淆绕过包含多条变体。
- 预期声明：每条样本固定 `expected_security_result`、稳定 `expected_error_code`、HTTP 状态、允许暴露内容、禁止泄漏标记、`expected_scope`、`expected_field_mask`、模型到达限制、Run 创建限制和回放限制，供阶段 1D/1E 的确定性防护测试复用。
- 自动化验收：`tests/test_p011_security_dataset.py` `6/6`；校验数据集版本和类别完整性、`synthetic=true`、`case_id` 唯一、禁止泄漏标记非空、错误码与契约目录 HTTP 状态一致、工作空间范围与恢复守卫存在，以及疑似真实凭证模式和非合成标识不存在。
- 当前边界：本节点只建立版本化安全与评估数据，不实现 Prompt 防火墙、真实模型安全评测、LLM Grading、工具执行拦截或安全运营平台；阶段 1D/1E 按 Fixture 接入确定性防护，真实供应商配置后再做模型安全表现评估。
- 提交：`2fef22d`。

### P0-12 供应链检查、阶段报告与范围冻结

- 状态：通过。
- SBOM：Python 和 Node 生产依赖均生成 CycloneDX 1.5 产物；生成结果移除时间、随机 UUID、用户名和本机绝对路径，并通过锁文件漂移检查。Python 锁文件包含 39 个跨平台生产组件，当前 macOS 环境许可证清单覆盖实际安装的 34 个适用组件；Node 清单覆盖 76 个生产/可选组件。
- 漏洞审计：首次 Python 审计发现 `pdfminer-six 20251107` 的 1 个和 `starlette 0.48.0` 的 6 个已知漏洞；升级 FastAPI、Starlette、pdfplumber 和 pdfminer-six 后，`pip-audit 2.9.0` 复查为 0。Node 生产依赖审计的严重、高危、中危和低危漏洞均为 0。
- 镜像扫描：Docker Scout 1.24.0 已安装，但本机未登录，镜像 CVE 扫描保持 `not_configured`，不能描述为通过；阶段 1A 必须接入可用的镜像扫描门禁。
- 许可证：Redis 7.4 只保留为阶段 0 本地验证依赖，阶段 1A 按 [`ADR-001`](../decisions/ADR-001-replace-redis-with-valkey.md) 独立替换为 Valkey 8.x；MinIO Server 在商业分发或网络服务前必须法律复核并保持 S3 Adapter 可替换边界；psycopg 发布前复核 LGPL 分发义务。
- 自动化验收：供应链专项测试 `4/4`，覆盖 CycloneDX 版本、可复现元数据、许可证完整性和漂移识别；安全升级后的 API、契约、Worker 和解析专项测试 `33/33`；统一门禁 pytest `96/96`，Ruff、mypy、前端测试/构建、架构、契约和供应链重新生成无 Diff 全部通过。
- 容器验收：API 与 Worker 镜像从冻结锁文件重建，镜像内确认 FastAPI `0.141.1`、Starlette `1.6.0`、pdfplumber `0.11.10` 和 pdfminer-six `20260107`；只重建两个应用容器后，`./platform doctor` 七项诊断全部通过，数据库和对象存储容器未重建。
- 范围冻结：阶段 1 保留个人/企业空间、复杂组织、字段级 ABAC、自定义菜单及页面/接口统一权限、自定义工作流、多级审批、知识入库、RAG 引用和 SSE 断点续传；SaaS、Go 运行层、Channel Gateway、Durable Run、真实多源连接器、LLM Grading、多模态图片问答和 Agent 自动写操作不进入阶段 1。
- 交付文档：[`docs/supply-chain/README.md`](../supply-chain/README.md)、[`docs/stages/stage-1-plan.md`](./stage-1-plan.md) 和 [`docs/stages/stage-1-report.md`](./stage-1-report.md)。
- 当前边界：真实模型供应商、AI 回答质量、镜像 CVE 和容量认证仍分别保持 `not_configured` 或 `not_run`；这些状态不冒充通过，也不阻塞阶段 0 技术验证结论。
- 提交：`928c9a6`。

### P0-13 前端 UI/UX 设计基线

- 状态：通过。
- 范围：记录阶段 0 运行中心的实际评审结果，并冻结阶段 1 正式应用壳层、动态菜单、响应式断点、加载/错误反馈、触控尺寸、键盘焦点、对比度和状态表达规则。
- 页面检查：`1440×900`、`390×844`、`375×812`、`844×390` 和 `768×1024` 视口均无横向溢出；容器化页面六项服务在健康状态下可读；浏览器控制台无错误或警告。
- 已确认优化：移动端不能隐藏主导航；动态菜单使用后端 `MenuRelease` 快照和 React Router `NavLink`；刷新与折叠按钮目标尺寸至少 `44×44px`；加载状态使用固定尺寸 Skeleton；503 响应区分 API 不可达与依赖降级；统一 `:focus-visible` 和 `aria-live`。
- 主色调决策：阶段 0 继续使用当前深绿/荧光绿临时方案，等阶段 1 业务页面齐备后统一评审主色调、字体、暗色模式和语义色 Token。
- 交付文档：[`docs/design/ui-ux-baseline.md`](../design/ui-ux-baseline.md)。
- 已知限制：本节点只建立设计和验收基线，不修改阶段 0 运行中心代码，不提前实现阶段 1 动态菜单或空间切换。
- 提交：`fdaefea`。

### P0-14 Web 前端代码规范

- 状态：通过。
- 范围：参考 `digitizing` 的成熟 React 工程实践，形成适配当前 AI 平台的目录职责、命名、严格类型、React/Hooks、状态边界、API/SSE、样式、测试和 Git 质量门禁。
- 采用内容：页面、组件、Hook、Store 和 Service 分层；公共组件兼容保护；文件规模预警；严格 TypeScript；Conventional Commit 和按风险补充测试。
- 排除内容：不引入 axios、ahooks `useRequest`、Less、TailwindCSS、styled-components、`@seakoi/console-kit`、`@seakoi/corebox` 或 Apifox 事实源，也不降级当前 React 19、React Router 8、Ant Design 6 和 Vite 8。
- 当前边界：本节点只建立规范和阶段 1 落地顺序，不修改阶段 0 Web 实现或依赖配置；当前实施节点仍为 `P0-06`。
- 交付文档：[`docs/governance/frontend-code-standards.md`](../governance/frontend-code-standards.md)。
- 验证内容：文档链接与路径检查、`git diff --check`，以及现有 Web 的 ESLint、TypeScript、Vitest 和生产构建。
- 提交：`c362020`。

### P0-15 后端代码规范

- 状态：通过。
- 范围：建立适用于 FastAPI 模块化单体、独立 Worker 和未来 Go 演进的后端代码规范，覆盖模块接口、Python 类型、模型转换、工作空间权限、事务、SQLAlchemy、Migration、Outbox、SSE、外部 Adapter、安全、可观测性和测试。
- 核心约束：Router 只做协议适配；Application 用例统一授权和事务；业务数据、审计事实与 Outbox 同事务；Repository 强制接收可信工作空间范围；Worker 按至少一次投递和幂等设计；语言间只共享版本化契约。
- 适度设计：按真实业务领域组织深模块，不建设万能 `BaseService`/`BaseRepository`，不要求简单健康检查机械套用完整分层，不提前创建空 Go 工程。
- 强制入口：根目录 `AGENTS.md`、API/Worker README 和项目 README 均已链接本规范，后续对 `apps/api`、`apps/worker` 和 Python 后端模块的新增修改必须执行。
- 交付文档：[`docs/governance/backend-code-standards.md`](../governance/backend-code-standards.md)。
- 验证内容：文档链接与路径、`git diff --check`、Ruff 格式与 Lint、mypy strict 和完整 pytest。
- 提交：`92a7f83`。

### P0-16 工程规则自动执行与 AI 安全基线

- 状态：通过。
- 统一入口：新增 `./scripts/verify` 和 `pnpm verify`，本地与未来 CI 复用同一检查顺序，不维护两套行为不同的门禁。
- 自动检查：实现 Python/React 模块依赖检查、FastAPI 完整 OpenAPI 漂移检查、相对 Git 基线的契约文件/路径/操作/响应/属性/类型/枚举/必填字段兼容检查，以及跟踪/待跟踪文件的敏感路径和常见凭证模式检查。
- 检查器测试：Python 反例覆盖 Domain 导入 FastAPI、API 直连 Infrastructure、契约删除枚举值、新增必填字段和私钥模式；Node `3/3` 覆盖允许页面使用 API、禁止公共组件静态/动态依赖页面。
- 决策治理：新增 ADR 模板和触发规则，关键框架、模块接口、数据写入权、协议主版本、安全边界、部署方式和跨语言迁移必须保留决策、指标与回滚依据。
- AI/RAG 安全：固定直接/间接 Prompt Injection、知识库投毒、跨空间召回、字段泄漏、引用伪造、数据外泄、资源耗尽和状态污染威胁；`P0-11` 负责合成攻击数据集，阶段 1D/1E 实现确定性后端防护。
- 阶段 1 预留：1A 建立 `ReleaseManifest`、契约生成无 Diff 和 CI 必需检查；1D 建立不可变 `AiRuntimeConfigVersion`，版本化 Prompt、模型路由、切片、索引、检索与安全配置。
- 当前边界：本节点不选择 CI 提供商，不联网新增契约生成或扫描依赖；依赖漏洞、许可证、SBOM 和完整 Secret Scanner 仍由 `P0-12`/阶段 1A 交付，不标记为已通过。
- 验证内容：`./scripts/verify` 明确以状态码 0 完成；Web Vitest `1/1`、Node 架构测试 `3/3`、pytest `18/18`，前端生产构建、Ruff、mypy、完整 OpenAPI 漂移、契约兼容、模块依赖、敏感文件和 Git Diff 检查全部通过。
- 提交：`3fffe1a`。

## 4. 当前限制

- 当前开发机系统 Python 为 3.14.3，项目固定 Python 3.12，并由 uv 管理项目解释器，不能使用系统 Python 作为验收环境。
- 未配置真实模型供应商，因此不能执行真实回答质量、真实模型成本和供应商兼容性结论。
- 未准备容量压测机，容量认证保持 `not_run`，不影响阶段 0 的本地功能与契约验证。

## 5. 阶段结论

阶段 0 的 16 个必需节点均已完成实现与验收，核心契约、高风险技术路径、工程规范、安全数据集和供应链边界已经形成可复现证据。阶段关闭记录和 `stage-0-complete` 标签完成后，阶段 0 范围正式冻结；后续改动必须按阶段 1 节点或范围变更规则实施。

进入阶段 1 后按 [`阶段 1 实施计划`](./stage-1-plan.md) 从 `P1A-01` 开始。阶段 1 的本地功能验收不依赖真实企业客户和专用压测机；模拟企业数据必须继续使用版本化合成数据。
