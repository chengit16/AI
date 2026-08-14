# 项目协作规范

## 目录职责

- `apps/web`：React Web 前端，只处理展示、交互和前端体验权限。
- `apps/api`：FastAPI 模块化单体和所有服务端安全边界。
- `apps/worker`：异步任务、文档解析、OCR 和索引处理入口。
- `packages/contracts`：OpenAPI、SSE、集成事件、错误码和跨语言类型契约。
- `docs`：中文设计、计划、决策、验证报告和运维文档。

业务模块不得绕过统一策略接口访问跨工作空间数据，不得直接调用模型供应商，也不得写入其他模块私有数据。

## 文档与注释

- 新增和修改的文档统一使用中文，代码标识符、命令和协议名称保留原文。
- 新增和修改的代码注释统一使用中文，说明业务规则、边界和设计原因。
- 自解释代码不添加复述行为的低价值注释。
- 所有手写 TS/TSX 和 Python 源码执行对应规范的文件头、公开接口、字段与复杂流程注释要求；不得以历史文件为由跳过。
- 注释结构门禁是 `./scripts/verify` 的阻断项；生成文件和第三方代码只按规范声明的范围排除。

## Web 前端代码规范

- `apps/web` 的新增和修改执行 `docs/governance/frontend-code-standards.md`。
- 前端接口以仓库 `contracts/` 为事实来源，不使用其他项目的 Apifox 定义或统一响应类型。
- 新代码保持 React 19、TanStack Query、Zustand、Ant Design 6、Fetch/SSE、UnoCSS 主样式路径和 CSS Token 技术边界，不照搬参考项目的旧版本或专属依赖。

## Python 后端代码规范

- `apps/api`、`apps/worker` 及 Python 后端模块的新增和修改执行 `docs/governance/backend-code-standards.md`。
- 后端按业务领域和数据写入权组织模块，严格执行可信 `RequestContext`、工作空间隔离、统一策略、事务、Outbox、Worker 幂等和稳定错误码规则。
- HTTP、SSE、事件和跨模块接口以仓库 `contracts/` 为事实来源，不得传递 ORM、Python 异常类、Pickle 对象或框架上下文。
- 每个 Python 后端节点至少通过 Ruff 格式与 Lint、mypy strict 和 pytest；涉及数据库、权限、Worker、SSE 或契约时追加对应集成与安全测试。

## 节点交付

执行 `docs/governance/delivery-and-git.md`：每个节点通过验收、同步文档后形成一次独立 Git 提交。未经验证的工作不得在进度看板中标记为完成。

## 自动质量门禁

- 本地和未来 CI 的统一入口为 `./scripts/verify`，不得维护一套行为不同的远程检查。
- 正式前后端分层目录创建后，模块依赖检查自动生效；禁止通过动态 Import、再导出或跨模块私有实现绕过。
- 契约变更必须通过完整实现一致性和相对基线的兼容检查；破坏性变化发布新主版本并提供迁移和回滚方案。
- 关键架构调整执行 `docs/governance/architecture-decisions.md` 并使用 ADR 记录原因、影响和验证指标。

## 安全基线

- 凭证、主密钥、真实个人资料和真实企业资料不得提交到仓库。
- 测试数据必须为合成数据并明确标记来源。
- 前端菜单隐藏不构成安全边界，接口、数据和字段权限均由后端执行。
