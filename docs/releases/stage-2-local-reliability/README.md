# 阶段 2 本地可靠性发布报告

## 1. 发布结论

阶段 2 本地可靠性版本为 `0.2.0`，`stage_reliability=passed`，阶段关闭以 `stage-2-complete` 标签冻结。该版本继承阶段 1 的个人空间和企业空间全部功能，并完成任务恢复、Worker Lane 隔离、索引巡检与重建、跨实例 SSE、可观测性、运营查询、数据生命周期、权限传播、运营工作台和联合故障演练。

该结论只适用于当前 macOS Apple Silicon 本地 Docker 环境和全合成数据：

| 验收域 | 状态 | 结论边界 |
| --- | --- | --- |
| `core_functional` | `passed` | 继承阶段 1 本地个人版与企业版功能结论 |
| `stage_reliability` | `passed` | 阶段 2 小规模可靠性、恢复、治理和运营门禁通过 |
| `provider_integration` | `not_configured` | 未配置真实 GPT 中转或国内供应商 |
| `ai_quality` | `not_configured` | Mock Provider 不替代真实质量、成本和数据政策验收 |
| `capacity_certification` | `not_run` | 未执行百万 Chunk 和正式并发容量认证 |
| Linux 宿主机验收 | `not_run` | 当前只完成 macOS + Docker Desktop 实测 |
| 镜像漏洞扫描 | `not_configured` | 未获镜像元数据外发授权，正式发布门禁保持阻断 |

因此，`0.2.0` 是可在本地继续建设的可靠性基线，不是可直接对外发布的生产制品。

## 2. 阶段 2 交付范围

- 任务阶段、不可变 Attempt、租约、有限重试、取消、超时、人工恢复和死信。
- Control、Parsing、OCR、Embedding、Indexing 五个 Worker Lane 与独立 Scheduler。
- 索引一致性巡检、差异修复、全量重建、不可见构建和原子发布。
- Valkey Pub/Sub 无正文唤醒、PostgreSQL SSE 事实、跨实例回放和有界轮询兜底。
- 结构化日志、OpenTelemetry Trace、Prometheus 指标、健康状态和告警规则。
- 审计、用量、Outbox 运营查询、幂等重放和消费回执巡检。
- 工作空间确定性导出、业务数据清除、保留期执行和不可变删除证明。
- 菜单、API、检索和字段投影四个授权表面的撤权传播与失败关闭。
- 任务、索引、Outbox、审计与用量、生命周期五类受控运营页面。
- 数据库、对象、索引、Worker、API、SSE、Valkey、Outbox 与授权联合故障演练。

阶段 2 未引入 SaaS、Go 接入层或运行层、真实多源连接器、LLM Grading、多模态图片问答、Channel Gateway、Durable Run、Agent 控制面或 Agent 自动写操作。

## 3. 最终验收

| 门禁 | 结果 |
| --- | --- |
| `./scripts/verify` | React `42/42`、Python `544/544`、Ruff 和 mypy strict `497` 个源文件，以及架构、注释、UnoCSS、契约、供应链和生产构建全部通过 |
| `./platform doctor` | Web、API、MinIO、Tika、PostgreSQL、Revision `20260815_0041`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过 |
| `./platform drill-stage-2` | `p2-11-v1` 场景 `12/12` 通过，执行前后各 13 项诊断通过 |
| 浏览器 | `1440×900` 与 `390×844` 下运行状态和五类运营页签可访问，单一 `h1`，无页面级横向溢出和控制台错误 |
| 开发供应链 | `development_status=passed` |
| 正式供应链 | `release_status=blocked`，镜像扫描 `not_configured`、Linux 验收 `not_run` |

第一次在受限沙箱执行全量 Python 测试时，本地端口访问被系统拒绝；不依赖本地服务的 `427` 项测试通过。随后在允许访问同一组本地 PostgreSQL、Valkey、MinIO 和 Tika 的环境中原样重跑，最终 `544/544` 通过。该环境限制不记为产品失败，正式结果以上表的完整重跑为准。

## 4. ReleaseManifest

- 构建输入：[`release-manifest-input.v1.json`](./release-manifest-input.v1.json)。
- 生成清单：[`release-manifest.v1.json`](./release-manifest.v1.json)。
- 兼容矩阵：[`compatibility-matrix.v1.json`](../../../contracts/release/compatibility-matrix.v1.json)。
- 源码与镜像基线：`7dfa517`。
- 数据库 Revision：`20260815_0041`。
- 运行时：Node.js `24.19.0`、Python `3.12.12`。
- 目标平台：当前本机 `linux/arm64` 容器。
- 清单自摘要：`sha256:17ae80eef51038152a502683c054c441a10346a45ed4db2105084ca4707b245d`。

四类源码摘要使用提交 `7dfa517` 和各 Dockerfile 的实际 `COPY` 输入边界计算：

```bash
git archive 7dfa517 package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc apps/web infra/nginx/default.conf | shasum -a 256
git archive 7dfa517 pyproject.toml uv.lock apps/api/src packages/backend/src alembic.ini infra/migrations contracts/errors contracts/authorization contracts/lifecycle contracts/observability contracts/fixtures/release-manifest.v1.valid.json contracts/release | shasum -a 256
git archive 7dfa517 pyproject.toml uv.lock apps/worker/src packages/backend/src contracts/observability | shasum -a 256
git archive 7dfa517 contracts packages/contracts | shasum -a 256
```

镜像摘要记录 2026-08-16 本地验收镜像的 Docker 内容 ID：

| 镜像 | 内容摘要 |
| --- | --- |
| `ai-intelligent-platform-api:latest` | `sha256:6503856c…dd2af7` |
| `ai-intelligent-platform-web:latest` | `sha256:f97a641f…cc40e8` |
| `ai-platform-worker:local` | `sha256:858ec39a…9d54d` |
| `ai-platform-tika:3.2.3-chi-sim` | `sha256:fc7acb68…68836e` |
| `pgvector/pgvector:pg16` | `sha256:a3625087…14c6b` |
| `minio/minio:RELEASE.2025-07-23T15-54-02Z` | `sha256:d249d1fb…acbc0` |
| `valkey/valkey:8.1.5-alpine` | 固定上游摘要 `sha256:918228e4…71861` |

本地内容 ID 不是注册表多架构摘要。正式发布必须由目标构建系统重新生成注册表摘要、镜像扫描、Linux 验收和发布级许可证证据；当前清单不能传给 `scripts/create_release_bundle.py` 冒充正式发布归档。

重新检查：

```bash
.venv/bin/python scripts/generate_release_manifest.py \
  --inputs docs/releases/stage-2-local-reliability/release-manifest-input.v1.json \
  --output docs/releases/stage-2-local-reliability/release-manifest.v1.json \
  --check
```

## 5. 验收索引

- [阶段 2 验证报告](../../stages/stage-2-report.md)：每个节点的实现边界、测试、容器、浏览器和故障演练证据。
- [项目进度看板](../../project-progress.md)：节点状态和 Git SHA。
- [本地 MVP 运维与故障处理手册](../../operations/local-mvp-operations.md)：运行、诊断、运营、演练、恢复和升级回退。
- [供应链与许可证基线](../../supply-chain/README.md)：SBOM、许可证、镜像扫描和正式发布边界。
