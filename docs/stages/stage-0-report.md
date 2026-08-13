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
- 提交：本节点提交完成后于 `P0-03` 回填。

### P0-03 最小应用健康链路

- 状态：通过。
- Web：React 19、TypeScript、Vite、React Router、TanStack Query、Zustand 和 Ant Design 运行中心。
- API：FastAPI 存活与就绪端点，OpenAPI 3.1 文档入口。
- Worker：独立健康状态模型和命令入口。
- 自动化验收：Vitest 1 项、pytest 3 项通过；ESLint、Ruff、mypy、TypeScript 和生产构建通过。
- 浏览器验收：1440×900 与 390×844 视口无横向溢出；API 状态和刷新操作正常；最终控制台无错误或警告。
- 已知限制：当前 Web 初始生产资源约 556 KB，其中 Ant Design vendor 约 474 KB；阶段 0 仅有一个轻量页面，不阻塞节点，后续按菜单路由实施页面级懒加载。
- 提交：本节点提交完成后于 `P0-04` 回填。

## 4. 当前限制

- 当前开发机系统 Python 为 3.14.3，项目固定 Python 3.12，并由 uv 管理项目解释器，不能使用系统 Python 作为验收环境。
- 未配置真实模型供应商，因此不能执行真实回答质量、真实模型成本和供应商兼容性结论。
- 未准备容量压测机，容量认证保持 `not_run`，不影响阶段 0 的本地功能与契约验证。
