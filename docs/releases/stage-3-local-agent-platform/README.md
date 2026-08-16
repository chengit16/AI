# 阶段 3 本地 Agent 平台发布报告

## 1. 发布结论

阶段 3 本地 Agent 平台版本为 `0.3.0`，`stage_agent_platform=passed`，阶段关闭以 `stage-3-complete` 标签冻结。该版本继承阶段 1 的个人/企业 MVP 与阶段 2 的可靠性能力，并完成 Agent 草稿、确定性测试、个人/企业审批、不可变 Release、Service 路由、Runtime 隔离、灰度回滚、统一服务出口、控制台和 Release 运营晋级门禁。

该结论只适用于当前 macOS Apple Silicon 本地 Docker 环境和全合成数据：

| 验收域 | 状态 | 结论边界 |
| --- | --- | --- |
| `core_functional` | `passed` | 继承阶段 1 本地个人版与企业版功能结论 |
| `stage_reliability` | `passed` | 继承阶段 2 小规模可靠性、恢复、治理和运营结论 |
| `stage_agent_platform` | `passed` | 阶段 3 Agent 发布、运行、灰度、回滚、服务出口和确定性运营门禁通过 |
| `provider_integration` | `not_configured` | 未配置真实 GPT 中转或国内供应商 |
| `ai_quality` | `not_configured` | Mock Provider、人工反馈和离线规则不替代真实模型质量验收 |
| `capacity_certification` | `not_run` | 未执行百万 Chunk 和正式并发容量认证 |
| Linux 宿主机验收 | `not_run` | 当前只完成 macOS + Docker Desktop 实测 |
| 镜像漏洞扫描 | `not_configured` | 未获镜像元数据外发授权，正式发布门禁保持阻断 |

因此，`0.3.0` 是可在本地继续建设受控工具执行能力的 Agent 平台基线，不是可直接对外发布的生产制品。

## 2. 阶段 3 交付范围

- 工作空间隔离的 Agent、可变草稿、发布候选和不可变 AgentRelease 生命周期。
- Prompt、模型、知识范围、工作流、只读工具、输出、安全与预算的完整发布配置。
- 固定测试集、五类必需测试、确定性阈值、个人所有者审批和企业多级审批。
- Service、版本化 Route、Runtime 精确绑定、控制面故障隔离、灰度、晋级和一键回滚。
- 自定义知识 Agent、场景应用和 Open API 三类统一服务出口及可恢复 SSE。
- Agent、测试、审批、服务、发布回滚和 AgentRelease 运营控制台。
- Release 级质量代理指标、延迟、错误、降级、成本比较和失败关闭晋级门禁。
- `p3-13-v1` 的 15 场景联合验收清单、失败关闭执行器和最小化本地证据。

阶段 3 未引入 SaaS、Go 接入层或运行层、真实多源连接器、LLM Grading、多模态图片问答、Channel Gateway、Durable Run 或 Agent 外部写操作。受控写工具、确认、幂等和任务状态机进入阶段 4。

## 3. 最终验收

| 门禁 | 结果 |
| --- | --- |
| `./scripts/verify` | React `53/53`、Python `665/665`、Ruff 和 mypy strict `607` 个源文件，以及架构、注释、UnoCSS、契约、供应链和生产构建全部通过 |
| `./platform doctor` | Web、API、MinIO、Tika、PostgreSQL、Revision `20260816_0052`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过 |
| `./platform accept-stage-3` | `p3-13-v1` 场景 `15/15` 通过，执行前后各 13 项诊断通过；证据固定提交 `bbf5303` 且 `repository_dirty=false` |
| 浏览器 | P3-11/P3-12 已在 `1440×900` 与 `390×844` 验收 Agent、服务和运营页面，无页面级横向溢出与控制台错误；P3-13 未修改前端产物 |
| 开发供应链 | `development_status=passed` |
| 正式供应链 | `release_status=blocked`，镜像扫描 `not_configured`、Linux 验收 `not_run` |

联合验收覆盖个人与企业审批、失败测试阻断、跨空间拒绝、Release 不可变、写工具拒绝、草稿执行拒绝、控制面故障、Run 精确绑定、在途收敛、灰度回滚、并发晋级、三类服务出口、控制台全链路和运营晋级门禁。最新本地证据位于被 Git 忽略的 `.ai-platform/evidence/p3-13-latest.json`，不保存测试输出、业务正文或主体标识。

## 4. ReleaseManifest

- 构建输入：[`release-manifest-input.v1.json`](./release-manifest-input.v1.json)。
- 生成清单：[`release-manifest.v1.json`](./release-manifest.v1.json)。
- 兼容矩阵：[`compatibility-matrix.v1.json`](../../../contracts/release/compatibility-matrix.v1.json)。
- 源码与联合验收基线：`bbf5303`。
- 数据库 Revision：`20260816_0052`。
- 运行时：Node.js `24.19.0`、Python `3.12.12`。
- 目标平台：当前本机 `linux/arm64` 容器。
- 清单自摘要：`sha256:60e5d17d345803503c8d959440e2f5d6887ad71f672a8b5741e2db48373e7c81`。

四类源码摘要使用提交 `bbf5303` 和各 Dockerfile 的实际 `COPY` 输入边界计算：

```bash
git archive bbf5303 package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc apps/web infra/nginx/default.conf | shasum -a 256
git archive bbf5303 pyproject.toml uv.lock apps/api/src packages/backend/src alembic.ini infra/migrations contracts/errors contracts/authorization contracts/lifecycle contracts/observability contracts/fixtures/release-manifest.v1.valid.json contracts/release | shasum -a 256
git archive bbf5303 pyproject.toml uv.lock apps/worker/src packages/backend/src contracts/observability | shasum -a 256
git archive bbf5303 contracts packages/contracts | shasum -a 256
```

镜像摘要记录 2026-08-16 本地验收镜像的 Docker 内容 ID：

| 镜像 | 内容摘要 |
| --- | --- |
| `ai-intelligent-platform-api:latest` | `sha256:1d2dd53e…2e40a2` |
| `ai-intelligent-platform-web:latest` | `sha256:310f1002…a972f3` |
| `ai-platform-worker:local` | `sha256:8e15f8c9…78ed50` |
| `ai-platform-tika:3.2.3-chi-sim` | `sha256:7e0cd0d1…d5c78` |
| `pgvector/pgvector:pg16` | `sha256:a3625087…14c6b` |
| `minio/minio:RELEASE.2025-07-23T15-54-02Z` | `sha256:d249d1fb…acbc0` |
| `valkey/valkey:8.1.5-alpine` | 固定上游摘要 `sha256:918228e4…71861` |

本地内容 ID 不是注册表多架构摘要。正式发布必须由目标构建系统重新生成注册表摘要、镜像扫描、Linux 验收和发布级许可证证据；当前清单不能传给 `scripts/create_release_bundle.py` 冒充正式发布归档。

重新检查：

```bash
.venv/bin/python scripts/generate_release_manifest.py \
  --inputs docs/releases/stage-3-local-agent-platform/release-manifest-input.v1.json \
  --output docs/releases/stage-3-local-agent-platform/release-manifest.v1.json \
  --check
```

## 5. 验收索引

- [阶段 3 验证报告](../../stages/stage-3-report.md)：每个节点的实现边界、测试、容器、浏览器和联合验收证据。
- [项目进度看板](../../project-progress.md)：节点状态和 Git SHA。
- [本地 MVP 运维与故障处理手册](../../operations/local-mvp-operations.md)：运行、诊断、阶段 2 演练、阶段 3 联合验收、恢复和升级回退。
- [供应链与许可证基线](../../supply-chain/README.md)：SBOM、许可证、镜像扫描和正式发布边界。
