# 阶段 4 本地受控工具执行发布报告

## 1. 发布结论

阶段 4 本地受控工具执行版本为 `0.4.0`，`stage_tool_execution=passed`，阶段关闭由 `stage-4-complete` 标签冻结。该版本继承阶段 1 的个人/企业 MVP、阶段 2 的可靠性与数据治理和阶段 3 的 Agent 发布平台，并完成受控工具目录、Run/Step/Attempt/ToolCall 状态事实、五个内部只读 Adapter、确定性计划、个人确认、企业审批、凭证边缘注入、幂等副作用、超时重试取消、结果运营、SSE、控制台和联合故障演练。

该结论只适用于当前 macOS Apple Silicon 本地 Docker 环境和全合成数据：

| 验收域 | 状态 | 结论边界 |
| --- | --- | --- |
| `core_functional` | `passed` | 继承阶段 1 本地个人版与企业版功能结论 |
| `stage_reliability` | `passed` | 继承阶段 2 小规模可靠性、恢复、治理和运营结论 |
| `stage_agent_platform` | `passed` | 继承阶段 3 Agent 发布、运行、灰度、回滚和服务出口结论 |
| `stage_tool_execution` | `passed` | 阶段 4 受控工具、确认审批、凭证、幂等副作用、取消恢复和运营控制台通过 |
| `provider_integration` | `not_configured` | 未配置真实模型供应商或真实外部工具系统 |
| `ai_quality` | `not_configured` | Mock Provider、本地确定性 Adapter 和合成故障不能替代真实模型质量验收 |
| `capacity_certification` | `not_run` | 未执行百万 Chunk 和正式并发容量认证 |
| Linux 宿主机验收 | `not_run` | 当前只完成 macOS + Docker Desktop 实测 |
| 镜像漏洞扫描 | `not_configured` | 未获镜像元数据外发授权，正式发布门禁保持阻断 |

因此，`0.4.0` 是可在本地继续建设阶段 5 质量、成本、隔离与合规能力的受控工具执行基线，不是可直接对外发布的生产制品。

## 2. 阶段 4 交付范围

- 版本化工具定义、工作空间目录、套餐和当前权限交集，以及 Release 精确工具白名单。
- 普通任务 Run、Step、Attempt、ToolCall 状态事实、租约、终态优先级和唯一写入权。
- 知识检索、授权文档片段、工作流状态、审批状态和套餐用量五个固定内部只读 Adapter。
- 模型结构化候选意图到冻结计划的确定性 Schema、资源、预算、当前 PDP 和策略门禁。
- 个人高风险确认和企业多级审批，绑定规范参数摘要、策略版本、权限和有效期。
- `credential_ref` 加密存储、轮换、撤销和调用边缘短时注入，凭证不进入 Prompt、日志、事件或响应。
- 副作用前置幂等事实、并发唯一调用所有者、响应丢失未知结果和人工核对恢复。
- Worker 有限重试、租约续期、取消观察、恢复 generation 和迟到结果终态优先级。
- 结果安全检查、完整用量、连续 SSE 游标、最小审计/Outbox 和低基数运营事实。
- 工具目录、执行计划、任务历史、详情、确认、取消和 SSE 控制台，以及统一菜单/API/字段权限。
- `p4-12-v1` 八类故障/六类样本演练和 `p4-13-v1` 十五场景阶段联合验收。

阶段 4 未引入任意 HTTP、SQL 或文件系统工具，未接入真实客户凭证或外部连接器，也未提前实现真实模型质量、成本治理、L3/L4 隔离、法规策略或私有化交付。

## 3. 最终验收

| 门禁 | 结果 |
| --- | --- |
| `./platform start` | 最终 Web、API、Worker、Migration 与 Tika 镜像重建并启动成功 |
| `./platform doctor` | Web、API、MinIO、Tika、PostgreSQL、Revision `20260816_0061`、Valkey、五个 Worker Lane 和 Scheduler 共 13 项通过 |
| `./platform accept-stage-4-tools` | `p4-12-v1` 八场景、九个 case 和六类故障样本通过，前后各 13 项诊断通过 |
| `./platform accept-stage-4` | `p4-13-v1` 十五场景 `15/15` 通过，完整覆盖 12 条阶段不变量和六项关闭门禁；前后各 13 项诊断通过 |
| 浏览器 | `1440×900` 与 `390×844` 下工具目录展示 5 个固定工具，工具任务空态和权限导航正常，无页面级横向溢出或控制台 warning/error |
| 开发供应链 | `development_status=passed` |
| 正式供应链 | `release_status=blocked`，镜像扫描 `not_configured`、Linux 验收 `not_run` |

六项关闭门禁为：未授权工具拒绝、未确认副作用拒绝、重复副作用为零、Step/Attempt 可追溯、凭证暴露为零，以及取消/超时后迟到结果不能覆盖终态。最新本地最小证据位于被 Git 忽略的 `.ai-platform/evidence/p4-12-latest.json` 和 `.ai-platform/evidence/p4-13-latest.json`，不保存测试输出、业务正文、参数正文或凭证明文。

## 4. ReleaseManifest

- 构建输入：[`release-manifest-input.v1.json`](./release-manifest-input.v1.json)。
- 生成清单：[`release-manifest.v1.json`](./release-manifest.v1.json)。
- 兼容矩阵：[`compatibility-matrix.v1.json`](../../../contracts/release/compatibility-matrix.v1.json)。
- 源码与联合验收基线：`62fac84`。
- 数据库 Revision：`20260816_0061`。
- 运行时：Node.js `24.19.0`、Python `3.12.12`。
- 目标平台：当前本机 `linux/arm64` 容器。
- 清单自摘要：`sha256:ecacb54f8fbd9e64fa38685e00fbcda4dc754b5c0e5d087a50210e241a3f2190`。
- P4-13 清单 SHA-256：`f9a80b18591bbc00394cdb3579dc370fbd1b5dacb612518996c025d77b546f69`。
- P4-13 证据 SHA-256：`5e7f0c9ca36ca1c7e30526e47e747b49278cf92978748623107dc5df5a626098`。

四类源码摘要使用提交 `62fac84` 和各 Dockerfile 的实际 `COPY` 输入边界计算：

```bash
git archive 62fac84 package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc apps/web infra/nginx/default.conf | shasum -a 256
git archive 62fac84 pyproject.toml uv.lock apps/api/src packages/backend/src alembic.ini infra/migrations contracts/errors contracts/authorization contracts/lifecycle contracts/observability contracts/fixtures/release-manifest.v1.valid.json contracts/release | shasum -a 256
git archive 62fac84 pyproject.toml uv.lock apps/worker/src packages/backend/src contracts/observability | shasum -a 256
git archive 62fac84 contracts packages/contracts | shasum -a 256
```

镜像摘要记录 2026-08-17 最终本地验收镜像的 Docker 内容 ID：

| 镜像 | 内容摘要 |
| --- | --- |
| `ai-intelligent-platform-api:latest` | `sha256:4914c16f…176237` |
| `ai-intelligent-platform-web:latest` | `sha256:ef214caa…3c651` |
| `ai-platform-worker:local` | `sha256:450f052a…e845a` |
| `ai-platform-tika:3.2.3-chi-sim` | `sha256:a1a44e7a…2b1e22` |
| `pgvector/pgvector:pg16` | `sha256:a3625087…14c6b` |
| `minio/minio:RELEASE.2025-07-23T15-54-02Z` | `sha256:d249d1fb…acbc0` |
| `valkey/valkey:8.1.5-alpine` | 固定上游摘要 `sha256:918228e4…71861` |

本地内容 ID 不是注册表多架构摘要。正式发布必须由目标构建系统重新生成注册表摘要、镜像扫描、Linux 验收和发布级许可证证据；当前清单不能传给 `scripts/create_release_bundle.py` 冒充正式发布归档。

重新检查：

```bash
.venv/bin/python scripts/generate_release_manifest.py \
  --inputs docs/releases/stage-4-local-tool-execution/release-manifest-input.v1.json \
  --output docs/releases/stage-4-local-tool-execution/release-manifest.v1.json \
  --check
```

## 5. 验收索引

- [阶段 4 验证报告](../../stages/stage-4-report.md)：每个节点的实现边界、测试、容器、浏览器和联合验收证据。
- [项目进度看板](../../project-progress.md)：节点状态和 Git SHA。
- [本地 MVP 运维与故障处理手册](../../operations/local-mvp-operations.md)：运行、诊断、阶段演练、恢复和升级回退。
- [供应链与许可证基线](../../supply-chain/README.md)：SBOM、许可证、镜像扫描和正式发布边界。
