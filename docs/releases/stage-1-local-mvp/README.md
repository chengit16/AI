# 阶段 1 本地 MVP 发布报告

## 1. 发布结论

阶段 1 本地 MVP 版本为 `0.1.0`，`core_functional=passed`。个人空间和企业空间共用同一核心实现，已完成账号与空间、复杂组织、RBAC/ABAC、自定义菜单与接口统一权限、知识生产、RAG 问答、SSE 断点续传、自定义工作流、多级审批、本地备份恢复和全业务响应式/可访问性闭环。

该结论只适用于当前 macOS Apple Silicon 本地 Docker 环境和全合成数据。以下状态保持不变：

| 验收域 | 状态 | 结论边界 |
| --- | --- | --- |
| `core_functional` | `passed` | 本地个人版、企业版、安全和恢复主流程通过 |
| `provider_integration` | `not_configured` | 未配置真实 GPT 中转或国内供应商 |
| `ai_quality` | `not_configured` | Mock Provider 不替代真实回答质量、成本和数据政策验收 |
| `capacity_certification` | `not_run` | 未执行百万 Chunk 和正式并发容量认证 |
| Linux 宿主机验收 | `not_run` | 当前只完成 macOS + Docker Desktop 实测 |
| 镜像漏洞扫描 | `not_configured` | 未获镜像元数据外发授权，正式发布门禁保持阻断 |

因此，`0.1.0` 是可本地运行和继续建设的 MVP，不是可以直接对外发布的生产制品。

## 2. 交付范围

- 个人空间与企业空间、企业成员生命周期、7 级以内通用部门树能力、角色继承、套餐与配额。
- 菜单草稿、审批、发布快照、回滚，以及页面、动作和 API 的统一权限绑定。
- 数据级和字段级 ABAC，跨空间、部门、密级、字段和资源版本越权失败关闭。
- 文档上传、扫描、解析、中文 OCR、切片、索引、发布、重试和来源追踪。
- 主备模型网关、自定义 `base_url`、加密 Key、能力探测、数据政策和不可变运行配置。
- 权限前过滤、混合检索、重排、受控精读、引用验证、Prompt Injection 与数据泄漏防护。
- 问答会话、持久化 SSE、15 秒心跳、24 小时保留、`Last-Event-ID` 回放、快照恢复、取消和反馈。
- 六类受限工作流节点、不可变发布、运行事实、多级审批、转交、撤回、超时和审批后恢复。
- `.aiprb` 加密备份/恢复/导入/导出、数据库升级回退和主密钥轮换。
- 八个正式页面、六类视口、键盘焦点、可访问名称、触控目标和 WCAG AA 主题对比度。

阶段 1 未引入 SaaS、Go 接入层或运行层、真实多源连接器、LLM Grading、多模态图片问答、Channel Gateway、Durable Run 或 Agent 自动写操作。

## 3. ReleaseManifest

- 构建输入：[`release-manifest-input.v1.json`](./release-manifest-input.v1.json)。
- 生成清单：[`release-manifest.v1.json`](./release-manifest.v1.json)。
- 兼容矩阵：[`compatibility-matrix.v1.json`](../../../contracts/release/compatibility-matrix.v1.json)。
- 源码基线：`04d1908`；其后的 `P1G-04` 文档提交不改变 Web、API、Worker 或契约源码。
- 数据库 Revision：`20260815_0035`。
- 目标平台：当前本机 `linux/arm64` 容器。

组件源码摘要使用与各 Dockerfile 实际复制边界一致的 `git archive HEAD <paths> | shasum -a 256` 计算；镜像摘要记录 2026-08-15 本地验收镜像的 Docker 内容 ID。清单由 `scripts/generate_release_manifest.py` 生成并通过 Schema、兼容矩阵和自摘要校验，不手工计算 `manifest_digest`。

该清单是阶段关闭所用的本地 MVP 可追溯证据。正式商业发布必须由目标构建系统重新生成注册表镜像摘要、多架构平台信息、镜像扫描和 Linux 宿主机证据；当前清单不能传给 `scripts/create_release_bundle.py` 冒充正式发布归档。

重新检查：

```bash
.venv/bin/python scripts/generate_release_manifest.py \
  --inputs docs/releases/stage-1-local-mvp/release-manifest-input.v1.json \
  --output docs/releases/stage-1-local-mvp/release-manifest.v1.json \
  --check
```

## 4. 验收索引

- [阶段 1 验证报告](../../stages/stage-1-report.md)：每个节点的测试、容器、浏览器和已知限制。
- [项目进度看板](../../project-progress.md)：节点状态和 Git SHA。
- [本地 MVP 运维与故障处理手册](../../operations/local-mvp-operations.md)：启动、诊断、备份恢复、密钥轮换和升级回退。
- [UI/UX 设计基线](../../design/ui-ux-baseline.md)：八页面、六视口、可访问性和主色调验收。
- [供应链与许可证基线](../../supply-chain/README.md)：SBOM、许可证、镜像扫描和正式发布边界。

