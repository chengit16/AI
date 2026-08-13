# 阶段 0 技术验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 0：需求基线与技术验证 |
| 状态 | 进行中 |
| 报告日期 | 2026-08-13 |
| `core_functional` | `not_run` |
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
- 提交：本节点提交完成后回填。

## 4. 当前限制

- 当前开发机系统 Python 为 3.14.3，项目固定 Python 3.12，并由 uv 管理项目解释器，不能使用系统 Python 作为验收环境。
- 未配置真实模型供应商，因此不能执行真实回答质量、真实模型成本和供应商兼容性结论。
- 未准备容量压测机，容量认证保持 `not_run`，不影响阶段 0 的本地功能与契约验证。
