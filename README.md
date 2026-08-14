# AI 智能平台

本仓库用于建设同时支持个人空间和企业空间的 AI 智能平台。产品采用浏览器访问的 Web 形态，首期以 Docker Compose 在本地运行。

阶段 0 已完成并冻结，当前进入阶段 1：工作空间、企业治理与知识问答 MVP。完整产品与架构设计见 [AI 智能平台 V2 架构设计](./ai-intelligent-platform-v2-design.md)，实时进度见 [项目进度看板](./docs/project-progress.md)。

## 文档入口

- [项目进度看板](./docs/project-progress.md)：阶段、节点状态、验收证据和 Git 提交索引。
- [交付与 Git 管理规范](./docs/governance/delivery-and-git.md)：节点完成定义、提交规则和文档更新要求。
- [阶段 0 实施计划](./docs/stages/stage-0-plan.md)：阶段 0 的建设顺序、节点交付物和完成门禁。
- [阶段 0 技术验证报告](./docs/stages/stage-0-report.md)：阶段 0 的实际环境、验证事实、限制和结论。
- [阶段 1 实施计划](./docs/stages/stage-1-plan.md)：个人版与企业版 MVP 的节点依赖、交付物和门禁。
- [阶段 1 验证报告](./docs/stages/stage-1-report.md)：阶段 1 的持续验证事实和状态口径。
- [供应链与许可证基线](./docs/supply-chain/README.md)：生产依赖 SBOM、漏洞审计和商业发布边界。
- [前端 UI/UX 设计基线](./docs/design/ui-ux-baseline.md)：布局、动态菜单、响应式、可访问性和阶段 1 优化清单。
- [Web 前端代码规范](./docs/governance/frontend-code-standards.md)：React、目录、状态、接口、样式、测试和质量门禁。
- [后端代码规范](./docs/governance/backend-code-standards.md)：Python 模块、权限、事务、数据、Worker、SSE、测试和未来 Go 边界。
- [工程规则自动执行基线](./docs/governance/engineering-guardrails.md)：统一验证、模块依赖、契约兼容和仓库安全检查。
- [架构决策管理规范](./docs/governance/architecture-decisions.md)：ADR 触发条件、评审和演进规则。
- [AI 与 RAG 安全威胁模型](./docs/security/ai-rag-threat-model.md)：提示注入、知识投毒、跨空间召回、引用和数据外泄防护。

## 当前原则

- 一个可验收节点对应一次独立 Git 提交。
- 节点提交前必须完成测试并同步相关文档。
- 未通过验收的工作不得在进度看板中标记为完成。
- 容量认证、真实模型供应商和企业客户不阻塞本地 MVP 功能建设；未执行项必须保持 `not_run` 或 `not_configured`。

## 本地运行

Docker Desktop 运行后执行：

```bash
./platform start
./platform doctor
```

默认访问地址为 `http://127.0.0.1:3000/status`。停止服务使用 `./platform stop`，本地数据会保留在 `.env` 的 `AI_PLATFORM_ROOT` 目录中。

平台管理员不依附个人或企业空间，只能由本地运维命令授予或撤销：

```bash
./platform admin grant user@example.com
./platform admin revoke user@example.com
```

接入 GPT 中转或国内 OpenAI-compatible 模型时，先在 `.env` 的 JSON 数组中显式配置允许访问的公网 HTTPS 域名，例如 `MODEL_PROVIDER_ALLOWED_HOSTS=["relay.example.com"]`。默认空数组会阻止所有真实供应商连接；`MODEL_PROVIDER_PROBE_TIMEOUT_SECONDS` 控制能力探测超时，允许范围为 1～30 秒，默认 10 秒。平台拒绝内网地址、混合 DNS、非 443 端口、重定向及未经数据政策审核的外发，不应通过放宽网络校验接入本地测试服务。
