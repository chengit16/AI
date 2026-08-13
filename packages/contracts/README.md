# 共享契约

本目录说明契约的工程消费方式；可机器校验的事实文件统一位于仓库根目录 `contracts/`。Python 与未来 Go 模块必须实现同一套契约，不得复制并分叉安全规则。

契约版本只允许兼容性新增字段。删除字段、字段改名、收紧既有枚举或改变字段语义时，必须发布新的主版本并提供迁移与回滚方案。

阶段 1 起，React 与 Python 消费类型由根目录 OpenAPI 统一生成：

```bash
pnpm contracts:generate
pnpm contracts:check
```

React 产物位于 `apps/web/src/api/generated/`，Python 产物位于本目录的
`src/ai_platform_contracts/`。二者均为生成文件，禁止手工修改；契约变化后必须重新生成并提交，
`./scripts/verify` 会拒绝生成漂移。契约工具链在仓库根目录独立固定
`openapi-typescript 7.13.0` 与其支持的 TypeScript `5.9.3`；React 应用仍使用 TypeScript
`6.0.2`，两套编译器不共享解析职责。
