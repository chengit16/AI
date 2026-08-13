# AI 智能平台

本仓库用于建设同时支持个人空间和企业空间的 AI 智能平台。产品采用浏览器访问的 Web 形态，首期以 Docker Compose 在本地运行。

当前处于阶段 0：需求基线与技术验证。完整产品与架构设计见 [AI 智能平台 V2 架构设计](./ai-intelligent-platform-v2-design.md)，实时进度见 [项目进度看板](./docs/project-progress.md)。

## 文档入口

- [项目进度看板](./docs/project-progress.md)：阶段、节点状态、验收证据和 Git 提交索引。
- [交付与 Git 管理规范](./docs/governance/delivery-and-git.md)：节点完成定义、提交规则和文档更新要求。
- [阶段 0 实施计划](./docs/stages/stage-0-plan.md)：阶段 0 的建设顺序、节点交付物和完成门禁。
- [前端 UI/UX 设计基线](./docs/design/ui-ux-baseline.md)：布局、动态菜单、响应式、可访问性和阶段 1 优化清单。
- [Web 前端代码规范](./docs/governance/frontend-code-standards.md)：React、目录、状态、接口、样式、测试和质量门禁。

## 当前原则

- 一个可验收节点对应一次独立 Git 提交。
- 节点提交前必须完成测试并同步相关文档。
- 未通过验收的工作不得在进度看板中标记为完成。
- 容量认证、真实模型供应商和企业客户不阻塞本地阶段 0。

## 本地运行

Docker Desktop 运行后执行：

```bash
./platform start
./platform doctor
```

默认访问地址为 `http://127.0.0.1:3000/status`。停止服务使用 `./platform stop`，本地数据会保留在 `.env` 的 `AI_PLATFORM_ROOT` 目录中。
