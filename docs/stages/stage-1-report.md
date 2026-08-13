# 阶段 1 验证报告

## 1. 报告状态

| 项目 | 当前值 |
| --- | --- |
| 阶段 | 阶段 1：工作空间、企业治理与知识问答 MVP |
| 状态 | 进行中 |
| 报告日期 | 2026-08-13 |
| 当前节点 | `P1A-01` 进行中 |
| `core_functional` | `not_run` |
| `provider_integration` | `not_configured` |
| `ai_quality` | `not_configured` |
| `capacity_certification` | `not_run` |
| Linux 宿主机验收 | `not_run` |

## 2. 验证环境基线

| 项目 | 当前值 |
| --- | --- |
| 主要开发环境 | macOS 26.5.2，Apple Silicon `arm64`，18 GB 内存 |
| Node.js / pnpm | 24.19.0 / 11.20.0 |
| 项目 Python | 3.12.12，由 uv 管理 |
| 容器运行时 | Docker Desktop 4.86.0，Docker Engine 29.7.2，Compose v5.3.1 |
| 数据范围 | 仅使用合成个人与企业数据 |
| 模型范围 | 默认 Mock Provider；真实供应商由平台配置后单独验收 |

## 3. 节点验证记录

节点开始实施后按 [`阶段 1 实施计划`](./stage-1-plan.md) 持续追加以下事实：完成日期、测试命令、结果计数、容器或浏览器检查、已知限制和提交 SHA。未实际执行的检查保持 `not_run`，未配置的外部能力保持 `not_configured`。

### P1A-01 阶段契约与 ReleaseManifest

- 状态：通过。
- 核心实体：在不破坏 V1 旧消费者的前提下，为核心契约新增 `Account`、`Workspace`、`Conversation` 和 `Message` 可选定义；业务实体携带工作空间、版本和审计字段，消息内容继续复用已冻结的 `MessagePart`。
- 发布清单：新增 `ReleaseManifest V1` 和兼容矩阵 Schema，固定 Web、API、Worker、契约、数据库 Revision、Node/Python 运行时、镜像引用、平台和 `sha256` 摘要。构建时间、主机路径和随机标识不进入清单，自摘要覆盖除摘要字段外的全部内容。
- 深模块接口：`ReleaseManifestService.build` 对输入排序并生成稳定摘要；`validate` 一次聚合摘要、Schema、矩阵、组件 SemVer、数据库、运行时和镜像不兼容原因，启动器和升级器无需复制版本规则。
- 生成骨架：`scripts/generate_release_manifest.py` 接受构建系统提供的不可变元数据，生成前执行兼容检查，并支持 `--check` 漂移门禁。仓库 Golden Manifest 只使用全合成摘要，不代表真实镜像或可发布产物。
- 错误契约：新增 `RELEASE_MANIFEST_INVALID` 和 `RELEASE_COMBINATION_INCOMPATIBLE`，区分输入/摘要无效与运行组合冲突。
- 自动化验收：契约与发布模块专项测试 `18/18`，统一门禁 pytest `104/104`；覆盖 JSON Schema、Golden Fixture、可复现排序、自摘要防篡改、有效组合和一次返回多项不兼容原因。前端测试/构建、Ruff、mypy strict、架构检查、Manifest 漂移和相对 `HEAD` 的同主版本兼容检查通过。
- 已知警告：Starlette `TestClient` 提示后续迁移 `httpx2`，已列入 `P1A-06` 工程依赖事项，不影响当前契约与发布模块行为。
- 当前边界：兼容矩阵记录阶段 0 关闭时的 Redis 7.4 运行组合，`P1A-02` 替换 Valkey 后必须同步；真实发布镜像摘要由后续构建环境提供，当前不把合成 Fixture 描述为发布清单；启动时自动装载与拒绝逻辑随正式应用装配节点接入。
- 提交：本提交。

## 4. 当前限制

- 当前没有真实模型供应商配置，不能给出真实供应商兼容性、质量、成本或数据政策结论。
- 当前没有独立 Linux 或容量压测机，不能给出 Linux 宿主机兼容和生产容量结论。
- 当前没有真实企业客户，阶段 1 使用固定的合成企业空间完成产品与安全验收。
- 镜像扫描工具尚未完成认证或本地可用配置，阶段 1A 必须如实补齐或保持失败状态。

## 5. 阶段结论

`not_run`。阶段 0 已关闭，当前从 `P1A-01` 开始记录。
