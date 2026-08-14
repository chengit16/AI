# 工程规则自动执行基线

## 1. 目的

本文档定义前端、后端、共享契约和仓库安全规则如何从书面规范转化为可重复执行的检查。开发者、本地自动化和未来 CI 使用同一个入口，避免本地与远程门禁分叉。

统一入口：

```bash
./scripts/verify
```

也可以通过根目录 package script 执行：

```bash
pnpm verify
```

统一入口优先使用 uv 已创建的项目 `.venv`，避免每个检查重复启动依赖解析；首次运行或项目虚拟环境不存在时回退到 `uv run --locked`，仍以 `uv.lock` 为唯一 Python 依赖事实来源。依赖变更后必须先执行锁定同步，不能使用未同步的旧虚拟环境验收。

## 2. 当前自动门禁

| 检查            | 实现                                            | 当前能力                                                                                       |
| --------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Git Diff        | `git diff --check`、`git diff --cached --check` | 同时检查未暂存与已暂存改动，阻止空白错误和冲突标记进入节点提交                                 |
| 仓库安全        | `scripts/check_repository_policy.py`            | 检查跟踪/待跟踪文件及完整 Git 历史中的敏感后缀、私钥头和常见真实令牌模式，定位时不输出凭证正文 |
| 契约生成        | `scripts/generate_contract_types.py --check`    | 固定生成 React TypeScript 与 Python `TypedDict` 消费类型，拒绝生成产物漂移                     |
| 供应链状态      | `scripts/check_release_readiness.py`            | 开发门禁校验本地可执行项；正式发布门禁额外要求镜像扫描和 Linux 验收证据                        |
| Python 架构     | `scripts/check_architecture.py`                 | 正式领域分层目录出现后自动检查 Domain、Application、API 与 Infrastructure 依赖方向             |
| React 架构      | `scripts/check-frontend-architecture.mjs`       | 解析静态 Import、再导出和字符串动态 Import，阻止公共层反向依赖页面等违规关系                   |
| UnoCSS 样式架构 | `scripts/check-frontend-styles.mjs`             | 固定唯一配置、关闭 Preflight、禁止额外预设和动态 Utility 拼接，保证生产构建可静态提取          |
| 契约漂移        | `tests/contract/test_contracts.py`              | FastAPI 完整 OpenAPI 输出必须与仓库基线一致；JSON Schema 和 Golden Fixtures 必须有效           |
| 契约兼容        | `scripts/check_contract_compatibility.py`       | 相对指定 Git 基线检查文件删除、路径/操作/响应删除、属性删除、类型变化、枚举收紧和新增必填字段  |
| Web 质量        | pnpm scripts                                    | Prettier、ESLint、TypeScript、Vitest 和生产构建                                                |
| Python 质量     | uv tools                                        | Ruff 格式、Ruff Lint、mypy strict 和完整 pytest                                                |

架构检查采用渐进生效：当前阶段 0 的过渡文件不会因为目标目录尚未建立而被迫重排；阶段 1 创建正式目录后，新增违规 Import 会立即失败。

## 3. 契约兼容基线

本地默认相对当前 `HEAD` 检查工作区契约变更：

```bash
uv run --locked python scripts/check_contract_compatibility.py HEAD
```

未来 CI 应将环境变量设置为目标分支的 Merge Base 或稳定发布 Tag：

```bash
AI_PLATFORM_CONTRACT_BASE_REF=origin/main ./scripts/verify
```

当前检查定位是阻止明显的同主版本破坏，不替代完整协议语义评审。以下变化必须发布新主版本并提供迁移和回滚方案：

- 删除契约文件、HTTP 路径、操作、既有响应、Schema 或属性。
- 修改既有字段类型、删除枚举值或新增必填字段。
- 收紧原先允许的附加属性或改变字段语义。
- 删除稳定错误码。

阶段 1A 已在独立工具链固定 `openapi-typescript 7.13.0`、其支持的 TypeScript `5.9.3` 和仓库 Python 生成器 `p1a-06-v1`，并生成 React 与 Python 消费类型；React 应用继续使用 TypeScript `6.0.2`。生成产物必须可重复，重新生成后不得产生未提交 Diff；未来 Go 复用同一契约和 Golden Fixtures。

## 4. 模块依赖门禁

Python 目标方向：

```text
api -> application -> domain
infrastructure -> domain
app -> api + application + infrastructure
```

React 目标规则包括：

- `api/` 不依赖页面、公共组件、路由和 Store。
- `components/` 与 `hooks/` 不依赖具体页面和路由。
- `store/` 不依赖页面、路由和 API 请求层。
- `types/` 与 `utils/` 不依赖 React 页面、组件、Store 或 API 请求层。
- 页面与应用壳层可以编排稳定公共层。

检查器只自动判断可证明的 Import 关系。业务模块越权访问其他模块私有表、浅层封装和职责混合仍需代码评审与集成测试判断。

## 5. 仓库安全边界

仓库 Secret Scanner 是发布前的凭证泄漏防线，不替代 SAST：

- 阻止 `.env`、私钥、证书密钥和平台主密钥等高风险文件被跟踪。
- 检查私钥头和常见令牌格式，不打印命中的密钥正文。
- `.env.example` 只允许包含无权限的合成本地默认值。
- 扫描所有可达 Git Blob，能够发现已从工作树删除但仍留在历史中的凭证；只报告 Blob 短 ID 和路径，不输出命中正文。
- 真实凭证一旦进入 Git 历史，应立即轮换，删除当前文件不能撤销泄漏。

`P1A-06` 已把状态清单接入 `./scripts/verify`。本地开发门禁要求契约生成、完整 Secret Scanner、SBOM、许可证和 `ReleaseManifest` 全部通过；正式发布还要求可信镜像扫描证据和 Linux 验收证据。镜像扫描当前因未获外发镜像元数据授权保持 `not_configured`，Linux 保持 `not_run`，因此正式发布门禁会明确失败，但不阻断后续本地功能节点。

## 6. 失败处理

- 任一检查失败时，节点只能保持“进行中”或“受阻”，不得标记完成或创建完成提交。
- 修复应聚焦本节点范围；格式化或生成命令造成无关大范围变化时必须缩小目标。
- 检查器误报需要通过新增最小反例测试修复，不能直接删除门禁。
- 确认需要改变规则时，先形成 ADR 或范围决策，再同步规范、检查器和测试。
- 容量、真实模型质量和外部供应商检查继续如实使用 `not_run`、`not_configured`，不能由本地 Mock 检查替代。

## 7. 阶段 1A 接入 CI 的要求

阶段 1A 需要在选定代码托管平台后，将 `./scripts/verify` 设为主分支合并必需检查，并补充：

1. Ubuntu 容器化运行，与 macOS 本地结果一致。
2. 契约基线使用目标分支 Merge Base，不使用易漂移的本地分支名。
3. 固定契约生成器和生成产物无 Diff 检查。
4. PostgreSQL/Testcontainers、Migration、权限、Outbox 和 SSE 集成测试。
5. 依赖漏洞、Secret Scanner、许可证和 SBOM 检查。
6. 失败日志不得输出凭证、Cookie、用户正文和受 ABAC 保护字段。
7. 主分支保护只允许全部必需检查通过后合并。

正式发布候选还需执行：

```bash
.venv/bin/python -m scripts.check_release_readiness --require release --check
.venv/bin/python scripts/create_release_bundle.py \
  --manifest artifacts/release/release-manifest.json \
  --readiness artifacts/release/readiness.json \
  --output artifacts/release/bundle
```

归档命令只接受 `release_status=passed` 的状态清单，并为 Manifest、SBOM、许可证、跨语言契约
类型和状态清单生成 `SHA256SUMS`。状态清单本身不能代替原始扫描报告；正式构建流水线需保存两类证据。

具体 CI 提供商、配置文件与分支保护在仓库远端确定后实施；当前统一脚本已经固定其本地接口。
