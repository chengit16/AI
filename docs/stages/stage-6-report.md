# 阶段 6 验证报告

## 1. 当前结论

| 项目 | 当前值 |
| --- | --- |
| 阶段状态 | 进行中 |
| 当前节点 | `P6A-03` 已完成；下一节点为 `P6A-04` |
| 第一版范围 | 阶段 A+B |
| 在线文档编辑器 | `POST-EDITOR-01`，第一版不实施 |
| 阶段 5 关系 | 非模型节点并行建设；AI 对外开通门禁不变 |
| 阶段结论 | `P6A-01`～`P6A-03` 已完成；阶段 6 整体仍进行中 |

## 2. 入口条件审计

- 阶段 1～4 已完成，本阶段复用身份、工作空间、组织、RBAC/ABAC、知识、解析索引、检索、审批、Agent、Worker、审计和数据生命周期底座。
- 阶段 5 的真实质量和真实价格仍受外部输入阻塞；该阻塞不被阶段 6 页面或合成数据结果覆盖。
- 参考站个人/企业功能和数据流已经完成有数据调研，差距基线见 [功能差距分析](../product/jitknow-feature-gap-analysis.md)。
- 当前仓库代码核对确认 `knowledge` 模块拥有 `KnowledgeBase`、`Document`、`DocumentVersion`、`DocumentSource` 与 `DocumentPublication`；`workflow` 拥有审批策略与 Runtime；新组织关系不建立第二份内容事实。

## 3. `P6A-01` 验证记录

### 3.1 交付物

- [阶段 6 第一版范围与信息架构基线](../product/stage-6-v1-scope-and-information-architecture.md)：冻结 D1～D14、页面动作、状态、流程和迁移规则。
- [ADR-016](../decisions/ADR-016-knowledge-organization-and-governance-boundaries.md)：固定唯一内容事实、组织关系、发布审批对象和回滚边界。
- [阶段 6 实施计划](./stage-6-plan.md)：建立 `P6A-01`～`P6B-07` 节点、依赖和门禁。
- [领域语言](../../CONTEXT.md)：补充文件夹、标签、收藏、回收站、企业分类、团队知识域和发布请求术语。
- 主设计、改造计划、交付规范和项目进度看板同步为阶段 6 正式启动状态。

### 3.2 场景审计

| 场景 | 预期结论 | 当前结果 |
| --- | --- | --- |
| 文件夹移动文档 | 只改变组织关系，不复制版本/对象/Chunk | `P6A-01` 设计通过；组织关系实现见 4.1，文件详情体验继续由 `P6A-03` 验收 |
| 标签删除 | 只删除绑定，不删除文档 | `P6A-01` 设计通过；实现与当前验证见 4.1～4.3 |
| 原文件夹已删除后恢复文档 | 回到默认根目录 | `P6A-01` 设计通过；实现与当前验证见 4.1～4.3 |
| 企业版本提交审批 | 上传解析不受阻，只有发布指针切换待审批 | 设计通过，代码待 `P6B-04` |
| 审批期间成员撤权 | 批准后发布前重新鉴权，失败不切换 | 设计通过，代码待 `P6B-04` |
| AI 企业大脑范围为空 | 返回空结果，不回退到全企业 | 设计通过，代码待 `P6B-06` |
| 跨空间关系或检索命中 | 阻断整次操作并告警 | 设计通过，代码与专项测试待后续节点 |
| 新建在线文档 | 第一版不存在入口和正文模型 | 范围通过 |

### 3.3 验证命令

`P6A-01` 为文档、范围和领域边界节点，已执行与风险匹配的定向门禁：

```bash
git diff --check
python scripts/check_repository_policy.py
rg -n "Codex调研-[0-9]" docs/product docs/stages docs/decisions CONTEXT.md ai-intelligent-platform-v2-design.md docs/project-progress.md
```

定向门禁结果：`git diff --check` 通过，仓库敏感信息扫描通过，未发现被禁止的“Codex调研-日期时间”数据前缀；文档中的相对链接和第一版禁入能力边界已人工核对。

同时尝试执行完整 `./scripts/verify`。前端格式、Lint、类型检查、构建和 `63/63` 个前端测试通过，Python 格式、Lint、mypy 和非基础设施测试通过；由于本机 PostgreSQL 未监听 `127.0.0.1:5432`，以及 Tika/MinIO 依赖未启动，整体验证报告 `229` 个 PostgreSQL 集成错误和 `8` 个外部服务失败，命令以非零状态结束。这些失败不涉及本节点新增代码，但 `P6A-02` 起必须在真实依赖可用后补做对应集成门禁。

## 4. `P6A-02` 验证记录

### 4.1 已实现范围

- Revision `20260823_0072` 建立 `knowledge_folders`、`knowledge_tags`、文档目录/标签绑定和成员收藏表；为每个存量空间创建确定性默认根目录，并将活动及回收站文档补入唯一主目录。生命周期表注册表同步升至 `v17`，五张新增工作空间表纳入导出、清除和覆盖检查。
- Knowledge 模块在同一事务中完成目录、标签、收藏、软删除、恢复、永久删除、审计和 Outbox；所有接口从可信 `RequestContext` 取得 `workspace_id`，目录/标签写入要求 Owner，文档关系继续执行资源级授权。
- 文档摘要新增当前目录、活动标签和当前账号收藏投影；查询使用相关子查询避免标签连接放大，并继续执行知识库、活动状态、工作空间和资源授权交集。
- 回收站恢复在原目录不可用时回落到确定性默认根目录；普通主目录解绑同样采用替换语义回落默认根目录，默认根目录自身不能解绑。
- 手工永久删除与保留期清理都在删除数据库事实前收集上传原件、解析产物和索引引用对象键，并统一发出 `knowledge.document.trash_purge_requested`；数据库删除与 Outbox 意图保持同一事务。
- 解析产物键由 API 与 Worker 共享规则根据工作空间、版本和任务身份确定；即使 Worker 尚未回写对象键，清理意图仍包含预期产物。永久删除拒绝运行中租约，保留期跳过运行中文档，失租 Worker 补偿删除自己刚写出的产物。
- Worker 新增有限批次、带时区截止时间、`SKIP LOCKED` 和可选工作空间边界的保留期任务；对象清理消费者严格验证事件删除事实、当前 `workspace_id` 前缀和规范对象键，任一键越界时整次失败关闭。
- 入库 Worker 领取任务时按 `workspace_id + document_id` 显式关联活动文档；回收站任务不能因同空间存在其他活动文档而被错误领取，已持有租约继续按失租规则收敛。
- 统一授权入口把目录和标签路径中的 `folder_id`、`tag_id` 冻结为 PDP 资源标识和属性；资源级策略与审计不再误用工作空间 ID。
- MinIO Adapter 复用 S3 删除不存在对象的幂等语义；对象存储或消费回执数据库暂时故障最多重试 3 次，只有全部对象删除成功后才提交专用消费回执，不为已删除文档重建通用资源投影。
- 知识页面新增知识库选择、目录树、目录创建/改名/移动/删除、标签管理、名称和标签筛选、单篇/批量移动与标签、收藏、文档/目录回收站以及桌面/移动布局；原上传、新版本、解析、发布和重试入口继续保留。
- OpenAPI 新增字段采用带默认值的 v1 兼容扩展，前端 Service 层归一化为页面确定结构；契约兼容检查相对 `HEAD` 通过。
- 文件夹 RAG 开关不进入本节点。`ADR-016` 明确 `KnowledgeBase` 是检索边界，文件夹只负责组织内容，不提供不影响真实检索的假开关。

### 4.2 已执行验证

```bash
pnpm --filter @ai-platform/web test
MYPYPATH=apps/api/src:apps/worker/src:packages/backend/src:packages/contracts/src \
  .venv/bin/pytest -q tests/unit/test_p6a02_knowledge_organization.py \
  apps/worker/tests/test_trash_retention.py apps/worker/tests/test_object_cleanup.py
MYPYPATH=apps/api/src:apps/worker/src:packages/backend/src:packages/contracts/src \
  .venv/bin/pytest -q tests/unit/test_p6a02_knowledge_organization.py \
  tests/unit/test_p1c01_resource_registry.py tests/unit/test_p1d03_ingestion_jobs.py \
  apps/worker/tests
MYPYPATH=apps/api/src:apps/worker/src:packages/backend/src:packages/contracts/src \
  .venv/bin/pytest -q apps/api/tests/test_authorization_api.py
.venv/bin/python scripts/check_contract_compatibility.py HEAD
AI_PLATFORM_TEST_DATABASE_URL='postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform' \
  AI_PLATFORM_TIKA_URL='http://127.0.0.1:9998' \
  AI_PLATFORM_MINIO_ENDPOINT='http://127.0.0.1:9000' ./scripts/verify
./platform status
```

验证结果：前端 `22` 个测试文件、`69/69` 个测试通过；P6A-02 组织、保留期和对象清理定向测试 `26/26` 通过，Worker、资源注册表与入库失租补偿扩大回归 `46/46` 通过，授权 API `7/7` 通过。真实 PostgreSQL 上的知识组织 `5/5`、入库 Attempt `4/4`、空库/存量库 Migration 往返 `8/8` 通过；Tika 真实解析链路 `9/9`、MinIO/对象清理/回收期 `18/18` 通过。生命周期 Registry 升至 `v17` 后，新增五表的导出、清除、运营菜单升级与法规治理联合回归 `15/15` 通过。

公共开发数据库已从 Revision `20260820_0071` 迁移到 `20260823_0072`，PostgreSQL、Valkey、MinIO、Tika、API 和 Web 均可访问。2026-08-25 在最终修复与文档同步后再次执行完整 `./scripts/verify`，React `69/69`、Python `971/971`、Ruff、mypy strict（`773` 个源文件）、契约兼容、资源注册表、架构、注释、UnoCSS、供应链和生产构建全部通过。

### 4.3 浏览器与真实数据流验证

2026-08-24～2026-08-25 使用本地合成账号和个人工作空间执行真实浏览器点击，未使用 Mock API 替代页面数据流。验证覆盖知识库选择、目录创建/改名/移动/软删除/恢复/永久删除、标签创建/改名/绑定/删除/恢复、文档单篇与批量移动/标签、收藏切换、文档删除/恢复/永久删除和 Outbox 人工重放。

| 场景 | 实际结果 |
| --- | --- |
| 目录组织 | 创建、改名、层级移动、文档移动、软删除、恢复和永久删除均成功；默认根目录继续存在，文档与版本/对象/Chunk 没有因目录操作被复制 |
| 标签生命周期 | 文档绑定数为 `1` 时删除标签，绑定数立即变为 `0`，文档仍存在；恢复标签后旧绑定保持为空，避免已删除关系被隐式复活 |
| 收藏 | 同一文档收藏与取消收藏均成功，列表计数和当前账号投影同步变化 |
| 文档回收站 | 软删除后回收站计数从 `0` 变为 `1`；恢复后返回活动列表；再次删除并永久删除时接口返回真实 `204 No Content`，页面提示“文档已永久删除”，回收站计数恢复为 `0` |
| 数据库事实 | 永久删除后对应文档行数为 `0`；数据库删除与 `knowledge.document.trash_purge_requested` Outbox 意图处于同一事务结果中 |
| 外部对象清理 | 事件 `6f7dfaa6-dbdc-4fd0-b0a5-bda3c9a7279e` 以原因码 `WORKER_RUNTIME_REFRESH` 重放后重新发布；专用消费者 `knowledge-object-cleanup-v1` 写入回执，原文件和确定性解析产物两个 MinIO 对象均返回不存在 |

双视口布局与控制台结果如下：

| 视口 | 页面横向边界 | 表格局部滚动 | 控制台与视觉检查 |
| --- | --- | --- | --- |
| `1440×900` | `body/root scrollWidth = 1440`，无页面级横向溢出 | 容器 `818 px`、内容 `1120 px`，局部横向滚动成立 | 知识组织导航、筛选、空状态和操作区无重叠；本地应用来源错误为 `0` |
| `390×844` | `body/root scrollWidth = 390`，无页面级横向溢出 | 容器 `328 px`、内容 `1120 px`，最大滚动距离 `792 px` 且实测到达末端，“更新时间/操作”列可见 | 顶栏、主操作、导航、筛选和空状态无重叠；本地应用来源错误为 `0` |

Chrome 扩展自身的令牌失效和请求超时日志来自 `chrome-extension://`，不属于当前 Web 应用；验收按 `http://127.0.0.1:3000` 来源筛选后无错误。完成检查后已恢复浏览器原视口。

## 5. `P6A-03` 验证记录

### 5.1 已实现范围

- 文档详情读模型聚合完整文档元数据、当前发布指针、文件夹、标签、收藏和不可变版本历史；每个版本展示来源文件、类型、大小、安全扫描、解析器、OCR、页数、内容块、最新索引状态、构建批次和 Chunk 数量，处理失败时展示稳定错误码和可公开错误详情。
- 详情响应在协议边缘明确排除 `original_object_key`、解析产物键和内容正文；领域内部对象键只服务于受控对象读取。OpenAPI、Python 契约和前端生成类型已重新生成并通过漂移与兼容检查。
- 新增 `knowledge.document.download` 权限，资源类型固定为 `document`，PDP 使用路径中的文档 ID 执行资源级授权。Revision `20260825_0073` 同步 Registry 26 的详情/下载绑定和菜单发布；权限回填只作用于存量系统所有者角色，自定义角色继续由管理员明确授权。
- 下载由同源 API 代理返回原文件字节，浏览器不接触 MinIO 地址或对象键。服务在对象读取前后分别打开短事务，重新校验活动成员、空间、知识库、文档、版本、来源和授权描述；任一关系变化、对象缺失或大小不一致均失败关闭。
- 下载成功审计只保存文档、版本、来源和文件大小业务事实，不保存对象键；跨空间详情和下载统一表现为不可见。
- 知识页面新增文档详情抽屉、版本下载、列表/卡片视图切换；卡片视图保留详情、选择、上传新版本、确认就绪、发布、移动、标签、收藏和删除等既有操作。上传、发布、重试和批量操作继续复用原事实服务，不新增在线正文、AI 写回或协作模型。

### 5.2 自动化与真实依赖

已执行以下定向验证：

```bash
pnpm --filter @ai-platform/web test --run
AI_PLATFORM_TEST_DATABASE_URL='postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform' \
  MYPYPATH=apps/api/src:apps/worker/src:packages/backend/src:packages/contracts/src \
  .venv/bin/pytest -q tests/integration/test_p1d01_knowledge_postgres.py
.venv/bin/ruff check apps/api/src/ai_platform_api/modules/knowledge \
  tests/integration/test_p1d01_knowledge_postgres.py
./scripts/verify
```

当前结果：前端 `23` 个测试文件、`73/73` 个测试通过；真实 PostgreSQL 知识集成 `7/7` 通过，覆盖详情聚合、下载字节、跨空间不可见、成功审计和 API 映射不泄露对象键。Revision `20260825_0073` 的非空升级、安全降级和再升级专项 `2/2` 通过，确认 Registry 26 文档详情/下载绑定、既有 Owner 授权和不可变菜单快照同步迁移，并在自定义授权或后续菜单发布存在时拒绝破坏性降级。此前扩大回归中的 PostgreSQL、入库任务和 MinIO 定向集成 `13/13`、资源注册表 `6/6` 均通过。最终原样 `./scripts/verify` 的 Web `73/73`、Python `974/974`、Ruff、mypy strict（`775` 个源文件）、注释、契约、权限注册表、生产构建和供应链检查全部通过。

### 5.3 浏览器与真实数据流

2026-08-25 使用全合成本地账号、个人空间、知识库和仓库固定合成文件执行真实页面与服务数据流，不使用 Mock API：

| 场景 | 实际结果 |
| --- | --- |
| 页面上传 | 在内置浏览器选择 `synthetic-text.pdf` 并提交，页面提示“文档已上传并进入解析队列”，列表状态从“待解析”真实流转到“待发布” |
| 首版发布与新版本 | 首个合成文档 V1 通过页面确认发布后成为当前发布版；V2 上传后完成解析和索引但保持待发布，详情同时展示 V2、V1 与唯一当前发布指针 |
| 处理链详情 | Markdown V1 展示 `markdown-structural-v1`、未使用 OCR、`1/4` 页数/内容块、构建批次 `#1` 和 `1` 个 Chunk；Text V2 展示 `plain-text-v1`、`1/2` 页数/内容块及独立索引状态 |
| 授权下载 | 页面点击下载后数据库新增成功审计；审计属性不含 `workspaces/...` 对象键。正式下载接口返回 SHA-256 `2f2537f0…8933870a`，与合成原件完全一致 |
| 卡片视图 | 列表切换为卡片后仍可打开同一详情、选择文档、发布及打开完整操作菜单；前端组件测试同时覆盖收藏回调和下载版本标识 |
| 协议泄露 | 详情 API 映射回归明确断言响应既不含 `original_object_key` 字段，也不含对象键值；浏览器详情只显示来源和安全元数据 |

双视口实际结果：

| 视口 | 页面与抽屉边界 | 视觉与控制台 |
| --- | --- | --- |
| `1440×900` | `body/root scrollWidth = 1440`，主内容 `1200 px`；详情抽屉、双版本处理链和卡片无页面级横向溢出 | 信息层级、按钮和版本分隔无重叠；修复 Drawer 弃用属性后，本地来源 warning/error 为 `0` |
| `390×844` | `body/root scrollWidth = 390`，抽屉宽度及右边界均为 `390 px`；卡片左右边界为 `31–359 px` | 顶栏、上传入口、组织导航、筛选、卡片、详情和下载按钮无重叠；本地来源 warning/error 为 `0` |

Chrome 扩展未开放本地文件 URL 权限时无法由自动化注入文件；这不作为产品上传失败。相同页面上传已改由内置浏览器真实完成，Chrome 继续用于 Blob 下载、桌面/移动布局和控制台验收。完成检查后已恢复浏览器默认视口。

## 6. 当前限制与下一步

- `P6A-03` 已通过代码、契约、真实依赖、数据流、统一门禁和双视口浏览器验收；下一节点为 `P6A-04` 个人工作台、统计和全局搜索。
- 第一版仍只管理上传文件及其不可变版本；在线正文、文档 AI 写回和多人实时协作继续保留在 `POST-EDITOR-01`、`POST-COLLAB-01`，本节点没有预建入口或空模型。
- 存量数据规模盘点必须在执行目标数据库上以只读摘要完成；当前文档不使用开发库数量推断生产规模。
- 阶段 5 的真实模型质量、价格、固定网络窗口和供应商授权样本仍保持原状态，不由本阶段关闭。

## 7. 阶段结论

`P6A-01` 已形成提交 `f68dc34`，`P6A-02` 已形成提交 `17be077`。`P6A-03` 已完成实现、真实数据流、浏览器验收和统一门禁，并由本节点独立提交交付。只有 `P6A-01`～`P6B-07` 全部通过各自门禁、阶段 A/B 联合验收通过、报告与进度看板完整、形成阶段关闭提交和 `stage-6-complete` 标签后，第一版才视为完成。
