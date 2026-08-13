# Web 前端代码规范

## 1. 目的与适用范围

本文档规定 `apps/web` 的代码结构、命名、React 组件、状态管理、接口访问、样式、测试和质量门禁。规范参考 `digitizing` React 前端项目的成熟工程实践，并根据 AI 智能平台当前技术栈和契约体系完成适配。

本规范是项目代码规范，不是 AI 对话规范。开发者、自动化工具和代码生成器提交到 `apps/web` 的代码都必须遵守。新增或实际修改的代码从本文档生效后执行；历史代码在对应业务节点中渐进迁移，不为了形式统一单独制造大范围重构。

规则优先级如下：

1. 当前需求和已经确认的产品、架构决策。
2. 本文档的前端代码规范。
3. [`docs/design/ui-ux-baseline.md`](../design/ui-ux-baseline.md) 的交互和可访问性规范。
4. 仓库 `contracts/` 下的 OpenAPI、SSE、事件、错误码和领域契约。
5. 目标模块现有且未被本规范否定的代码模式。

## 2. 技术基线

| 领域 | 当前基线 | 约束 |
| --- | --- | --- |
| 运行时 | Node.js 24、pnpm 11 | 以根目录版本约束和锁文件为准 |
| 框架 | React 19、TypeScript | 只使用函数组件和 Hooks |
| 构建 | Vite 8 | 不在业务模块中读取 Node.js 运行时 API |
| 路由 | React Router 8 | 页面必须支持 URL 直达和浏览器返回 |
| 服务端状态 | TanStack Query 5 | 查询缓存、刷新、重试和 Mutation 统一管理 |
| 客户端状态 | Zustand 5 | 只保存跨组件或跨路由的客户端状态 |
| UI | Ant Design 6 | 主题通过 `ConfigProvider` 和语义 Token 管理 |
| 图标 | `lucide-react` | 统一使用线性 SVG 图标，不使用 Emoji 充当结构图标 |
| 样式 | CSS、CSS Modules、Ant Design Token | 不新增 Less、TailwindCSS 或 styled-components |
| 测试 | Vitest、Testing Library | 测试用户可观察行为，不依赖组件内部实现 |

版本升级必须先验证 React、TypeScript、Vite、Ant Design 和测试工具的兼容性，不因参考项目使用旧版本而降级当前技术栈。

## 3. 目录结构与职责

阶段 1 的目标目录如下：

```text
apps/web/src/
├── app/                         # 应用壳层、Provider、全局初始化和错误边界
├── routes/                      # 静态入口、动态菜单路由和路由守卫体验层
├── pages/                       # 按业务域组织的路由页面
│   └── {domain}/{feature}/
│       ├── index.tsx            # 页面编排
│       ├── useFeature.ts        # 页面业务 Hook，可选
│       ├── config.ts            # 页面配置，可选
│       ├── types.ts             # 页面共享类型，可选
│       ├── utils.ts             # 页面纯函数，可选
│       └── components/          # 页面私有子组件
├── components/                  # 跨页面公共组件
├── api/
│   ├── client.ts                # REST 请求、错误标准化和公共请求上下文
│   ├── sse.ts                   # SSE 连接、游标和取消能力
│   └── services/{domain}/       # 按业务域组织的接口函数
├── hooks/                       # 跨页面复用 Hook
├── store/                       # Zustand 客户端状态
├── styles/
│   ├── tokens.css               # 语义颜色、间距、圆角、层级和动效 Token
│   └── global.css               # Reset、基础排版和全局可访问性样式
├── types/                       # 跨业务域共享的前端类型
├── utils/                       # 无 React 依赖的纯函数
└── test/                        # 测试环境和公共测试工具
```

目录职责必须满足以下边界：

- `pages/` 可以组合公共组件、业务 Hook 和 Service，但不能创建新的 HTTP 客户端。
- `components/` 不直接依赖具体路由页面，不包含特定页面的接口参数拼装。
- `api/services/` 只负责类型化请求和响应，不处理 Toast、导航或页面状态。
- `store/` 不保存可以由 URL、TanStack Query 或表单状态直接表达的数据。
- `utils/` 必须是可独立测试的纯函数，不读取 React Context 或 Zustand Store。
- 一个能力只在一个层级维护，禁止为调用方便复制常量、类型、格式化函数或权限判断。

当前 `App.tsx`、`api/health.ts` 和 `styles.css` 是阶段 0 过渡结构。进入阶段 1 正式应用壳层节点时按上述目录迁移，不单独重写历史文件。

## 4. 命名规范

### 4.1 文件与目录

| 类型 | 规则 | 示例 |
| --- | --- | --- |
| 公共组件目录和组件文件 | PascalCase | `WorkspaceSwitcher/WorkspaceSwitcher.tsx` |
| 页面业务域和功能目录 | camelCase | `knowledgeBase/documentList/` |
| 页面入口 | 固定 `index.tsx` | `pages/knowledgeBase/documentList/index.tsx` |
| Hook | `use` + PascalCase 语义 | `useDocumentList.ts` |
| Zustand Store | `use` + PascalCase + `Store` | `useWorkspaceStore.ts` |
| API Service | camelCase | `knowledgeBase.ts`、`workspaceMember.ts` |
| 类型文件 | 固定 `types.ts` | `types.ts` |
| 配置和工具 | 固定或 camelCase | `config.ts`、`queryKeys.ts`、`formatCitation.ts` |
| CSS Modules | 与组件同名 | `WorkspaceSwitcher.module.css` |

### 4.2 标识符

- 组件、类型和枚举使用 PascalCase。
- 函数、变量、对象和数组使用 camelCase。
- 常量使用能表达领域语义的 camelCase；只对真正全局且不可变的原始常量使用 `UPPER_SNAKE_CASE`。
- Hook 必须以 `use` 开头，Store Hook 必须以 `Store` 结尾。
- 布尔值使用 `is`、`has`、`can`、`should` 或 `allow` 前缀。
- 用户事件处理函数使用 `handleXxx`，传给组件的回调属性使用 `onXxx`。
- 请求参数、响应和实体类型分别使用 `XxxParams`、`XxxResponse`、`Xxx`；避免无业务含义的 `Data`、`Info` 和 `Item`。
- `permission_code`、`workspace_id` 等外部契约字段不得为了前端风格擅自改名；适配转换只能在 API 边界集中处理。

## 5. TypeScript 规范

- 保持 `strict: true`，禁止为了绕过错误关闭严格模式。
- 禁止使用 `any`；外部未知数据先使用 `unknown`，经过 Schema、类型守卫或明确校验后再使用。
- 优先使用联合类型表达有限状态，例如 `"idle" | "running" | "failed"`，不使用含义不明的数字魔法值。
- 对外暴露的组件 Props、Hook 返回值和 API 请求/响应必须有显式类型。
- 只在当前文件使用的类型就近声明；跨文件类型放在同目录 `types.ts`；跨业务域类型优先来自生成契约或 `src/types/`。
- 类型导入使用 `import type`，避免产生不必要的运行时代码。
- 不使用非空断言 `!` 掩盖未验证状态；应用入口等框架保证存在的单点例外必须保持局部且可解释。
- 不使用 TypeScript `enum` 表达可序列化契约，优先使用 `as const` 对象和联合类型。
- 新增质量配置时启用并保持 `noUnusedLocals`、`noUnusedParameters` 和 `noFallthroughCasesInSwitch`；以下划线开头的参数只用于接口签名要求但当前无需消费的场景。

## 6. 文件职责与规模预算

行数用于发现职责膨胀，不作为机械拆文件的唯一理由：

| 文件类型 | 推荐范围 | 预警范围 | 处理要求 |
| --- | ---: | ---: | --- |
| 页面入口 `index.tsx` | `≤150` | `151–250` | 抽取业务 Hook、配置或大块子组件 |
| 页面私有组件 | `≤200` | `201–350` | 按独立交互或展示区块拆分 |
| 公共组件 | `≤300` | `301–500` | 保持对外 API，拆内部 Hook、类型和子组件 |
| 业务 Hook / Zustand Store | `≤300` | `301–500` | 拆查询、Mutation、派生状态或领域动作 |
| 工具文件 | `≤300` | `301–500` | 按领域语义拆分，禁止形成通用杂物箱 |

达到预警范围时，代码评审必须判断是否混合了以下职责：页面编排、展示、服务端请求、客户端状态、配置、类型和纯函数。超过预警上限的文件不得继续引入新职责；配置、Schema、生成契约和稳定的大型字典不按普通业务文件行数衡量。

页面入口只负责调用 Hook、组合组件、传递 Props 和表达页面骨架。以下内容应移出入口：

- 多个无关请求和 Mutation 的参数拼装。
- 超过 5 项的列、表单、菜单或状态配置。
- 与 React 无关的数据转换和校验函数。
- 大型弹窗、抽屉、详情或复杂表格单元格。
- 跨页面复用的状态和业务动作。

## 7. React 组件与 Hooks

### 7.1 组件

- 使用函数组件，不使用 Class Component。
- 一个文件只维护一个主要组件；简单且只服务于主组件的私有展示函数可以保留在同文件。
- 组件 Props 表达业务语义，不暴露内部 DOM、缓存或状态实现细节。
- 公共组件优先使用具名导出；路由懒加载需要默认导出时可在路由边界建立轻量适配。
- 列表项必须使用稳定业务 ID 作为 `key`，禁止使用数组下标作为可变列表的 key。
- 不在渲染过程中修改状态、缓存、Store、浏览器 API 或发起请求。
- 能由 Props、Query 数据或已有 State 推导的值直接计算，不重复存入 State。
- 条件分支复杂时优先使用守卫返回或拆分子组件，避免多层嵌套三元表达式。

### 7.2 Hooks

- Hooks 必须无条件地在组件或自定义 Hook 顶层调用。
- `useEffect` 只用于同步外部系统，例如订阅、计时器、浏览器 API 和命令式第三方组件；用户动作由事件处理函数触发。
- Effect 必须清理订阅、定时器、SSE、事件监听和可取消请求。
- 不为了形式统一滥用 `useMemo`、`useCallback` 和 `memo`；只有存在实际计算成本、稳定引用契约或已验证重渲染问题时使用。
- 自定义 Hook 应封装可描述、可复用的状态行为，不把任意代码片段改名为 Hook。
- Hook 返回值使用有名称的对象；只有类似 `useState` 的稳定二元语义才使用元组。
- 异步回调必须考虑组件卸载、请求竞态、重复提交和过期响应。

### 7.3 公共组件兼容性

修改 `components/` 下已经被复用的公共组件时，默认保护：

1. Props 字段、类型、默认值和必填性。
2. `ref` 暴露的方法签名。
3. `onChange`、`onConfirm` 等回调参数和触发时机。
4. 导出名称和导出路径。
5. 被外部样式或测试依赖的稳定语义标识。

破坏性修改必须有明确迁移节点、调用方清单和回归测试，不能作为内部重构的附带变化。

## 8. 状态管理边界

| 状态类型 | 归属 | 示例 |
| --- | --- | --- |
| 服务端状态 | TanStack Query | 工作空间、菜单快照、知识库列表、审批记录 |
| URL 状态 | React Router | 当前页面、分页、筛选、Tab、可分享查询条件 |
| 表单状态 | Ant Design Form 或页面表单组件 | 输入值、校验、脏状态 |
| 局部交互状态 | `useState` / `useReducer` | 弹窗开关、当前选择、临时展开状态 |
| 跨路由客户端状态 | Zustand | 当前空间、侧栏状态、用户界面偏好 |

必须遵守：

- 不把 TanStack Query 数据复制到 Zustand。
- 可深链接、可刷新恢复的状态优先进入 URL，不放临时 Store。
- Zustand Store 暴露领域动作，组件使用选择器订阅最小状态片段。
- Store 不直接渲染 Toast、不操作路由 DOM，也不持有不可序列化的组件实例。
- Query Key 按业务域集中管理，包含影响结果的 `workspace_id`、筛选、分页和版本参数。
- Mutation 成功后精确更新或失效相关 Query，不能无差别清空全部缓存。
- 查询必须区分初次加载、后台刷新、空数据、错误和降级状态。

## 9. API、契约与 SSE

### 9.1 事实来源

AI 平台的接口事实来源按以下顺序确定：

1. 仓库 `contracts/openapi/`、`contracts/sse/`、`contracts/errors/` 和领域 JSON Schema。
2. FastAPI 生成并通过一致性测试的 OpenAPI。
3. 已批准的架构决策和业务规格。

不使用 `digitizing` 的 Apifox 项目、接口路径、字段或统一响应类型，不凭命名规律猜测接口。

### 9.2 分层规则

- REST 请求统一经过 `api/client.ts`，业务接口放在 `api/services/{domain}/`。
- 组件和 Zustand Store 禁止直接调用 `fetch`；页面通过 TanStack Query 和 Service 使用接口。
- SSE 使用独立的 `api/sse.ts` 或领域流式客户端，统一处理 `Last-Event-ID`、重连、取消、事件顺序和错误状态。
- Service 使用具名导出，一个函数表达一个稳定接口能力。
- 请求参数和响应结果必须类型化，优先消费契约生成类型，不重复手写同名模型。
- HTTP 非 2xx 响应先解析平台标准错误体，再抛出统一的前端错误对象；不能丢失 `error_code`、Trace ID 和可恢复信息。
- `workspace_id`、请求 ID、CSRF 和同源凭证策略在 Client 层统一处理，页面不得各自拼装安全请求头。
- 请求应接收或传递 `AbortSignal`，支持路由切换、查询取消和用户主动停止。
- 上传、下载、SSE 和普通 JSON 请求使用各自明确的响应处理，不在页面中根据猜测判断内容类型。

### 9.3 命名示例

| 能力 | 函数名 | 类型名 |
| --- | --- | --- |
| 获取列表 | `getKnowledgeBaseList` | `GetKnowledgeBaseListParams`、`KnowledgeBaseListResponse` |
| 获取详情 | `getKnowledgeBaseDetail` | `KnowledgeBaseDetail` |
| 创建 | `createKnowledgeBase` | `CreateKnowledgeBaseParams` |
| 更新 | `updateKnowledgeBase` | `UpdateKnowledgeBaseParams` |
| 删除 | `deleteKnowledgeBase` | `DeleteKnowledgeBaseParams` |
| 建立流式回答 | `streamConversationAnswer` | `ConversationStreamOptions` |

HTTP Method 和参数位置以 OpenAPI 契约为准，不根据 CRUD 名称自行推断。

## 10. UI 组件与图标选型

组件选型顺序为：

1. 检查 `src/components/` 是否已有满足语义和交互要求的公共组件。
2. 使用 Ant Design 6 的稳定组件和主题能力。
3. 使用 `lucide-react` 提供的统一图标。
4. 前三者不能表达领域交互时，再新增就近业务组件或公共组件。

禁止为了遵循参考项目引入 `@seakoi/console-kit` 或 `@seakoi/corebox`。禁止重复封装没有额外业务语义的 Ant Design 组件；公共封装必须至少统一一个真实的跨页面问题，例如权限、错误反馈、可访问性、字段约束或稳定业务交互。

详细布局、触控尺寸、响应式和可访问性要求执行 [`docs/design/ui-ux-baseline.md`](../design/ui-ux-baseline.md)。

## 11. 样式规范

- 全局语义变量统一放在 `styles/tokens.css`，Ant Design 组件 Token 统一放在主题配置中。
- 页面和组件新增样式优先使用 `*.module.css`；应用壳层和全局语义类可以使用经过命名约束的普通 CSS。
- 使用 CSS 自定义属性表达颜色、间距、圆角、阴影、层级和动效，业务组件不散落重复十六进制颜色。
- 不新增 Less、SCSS、TailwindCSS、UnoCSS 或 styled-components，除非经过独立架构决策确认迁移收益。
- 禁止使用内联样式承载可复用视觉规则；只允许动态尺寸、坐标或组件 API 明确要求的局部值。
- 禁止无理由使用 `!important`；覆盖 Ant Design 时优先使用主题 Token、组件 Token或局部作用域容器。
- Class 名应表达组件和状态语义；普通 CSS 延续 BEM 风格，CSS Modules 使用 camelCase 局部名称。
- 动画只使用 `transform` 和 `opacity` 等低成本属性，并支持 `prefers-reduced-motion`。
- 主题颜色在阶段 1 业务页面齐备后统一评审，当前阶段不因代码规范调整主色调。

## 12. 导入、导出与格式化

- 格式由 Prettier 统一处理，禁止通过手工对齐空格形成视觉表格。
- 当前项目保持双引号、分号、2 空格缩进、尾逗号和 LF 行尾，避免对历史文件产生无意义格式变化。
- 导入按“第三方依赖、项目绝对路径、当前模块相对路径、样式和资源”组织，各组之间空一行。
- 同一模块不得重复导入，类型使用 `import type`。
- 阶段 1 配置 `@/` 指向 `apps/web/src/` 后，跨业务域导入使用别名；同目录内部继续使用相对路径。
- 不通过多层 Barrel File 隐藏依赖和制造循环引用；公共模块入口可以维护稳定的显式导出。
- 暂不引入会大规模重排文件的 Import Sort 插件；需要机械排序时单独评估并形成独立格式化节点。

## 13. 注释规范

- 新增和修改的代码注释统一使用中文。
- 注释说明业务规则、关键流程、设计原因、权限边界、异常降级和非直观兼容处理，不复述代码表面行为。
- 公共组件、公共 Hook、API Client、复杂 Service 和容易误用的工具函数应提供简洁 JSDoc。
- 私有小组件、自解释函数和简单 Props 不强制逐字段写 JSDoc。
- TODO 和 FIXME 必须包含责任人或待确认角色、日期和退出条件，例如：`TODO(@frontend 2026-09): 菜单快照接口稳定后删除兼容分支`。
- 协议字段、固定错误信息、第三方许可证和生成文件注释保持原始格式。

具体要求同时执行仓库根目录 `AGENTS.md` 和本机全局注释规范；发生冲突时采用更严格且不会产生低价值注释的规则。

## 14. 错误处理、安全与权限

- 应用壳层和高风险功能区设置 Error Boundary，错误页提供恢复或返回路径。
- 不在控制台、URL、Toast、前端日志和错误详情中输出 API Key、Cookie、Authorization 或敏感业务字段。
- 浏览器登录凭证使用服务端 Session、HttpOnly Cookie 和 CSRF 防护，禁止把长期 JWT 保存到 `localStorage`。
- 禁止直接渲染未清洗 HTML；必须展示富文本时使用经过批准的清洗方案和内容安全策略。
- 菜单隐藏和按钮禁用只改善体验，不构成授权；直接访问 URL 和调用接口仍由后端策略、数据权限和字段级 ABAC 拒绝。
- 无权限、无菜单、资源不存在、工作空间已切换和 Session 失效是不同状态，必须使用对应错误码和恢复路径。
- 测试和示例只能使用合成数据，不把真实个人、企业或供应商资料写入前端 Fixture。

## 15. 性能规范

- 按菜单路由和大功能区进行 `lazy`/动态导入，避免所有业务页面进入首屏 Bundle。
- 不预先引入整个图标库、图表库、编辑器或文档预览器；使用可静态分析的按需导入。
- 50 项以上且行内容复杂的长列表评估虚拟化，分页业务默认不同时渲染全部数据。
- 图片声明稳定尺寸并优先使用合适格式，避免布局偏移。
- 搜索、滚动和 Resize 等高频事件按实际需要防抖或节流。
- 性能优化必须由构建报告、React Profiler 或实际指标证明，不进行无依据的全局 Memo 化。
- 页面必须区分初次加载和后台刷新，刷新不能无理由清空已有内容造成闪烁。

## 16. 测试规范

- 新功能和缺陷修复应按风险补充测试；权限、菜单、工作空间隔离、SSE 恢复、表单提交和关键错误恢复不能只依赖人工检查。
- 组件测试使用 Testing Library，以角色、标签和用户可见文本查找元素，避免依赖内部 Class 和实现细节。
- API 和 Query 测试在 Service 或网络边界 Mock，不在组件中 Mock 内部 Hook 实现。
- 测试必须覆盖正常、加载、空、错误、无权限和关键边界状态中与本次功能相关的部分。
- 时间、UUID、随机数和网络结果在测试中必须可控，禁止依赖真实供应商或真实客户数据。
- 每个前端节点至少执行：

```bash
pnpm --filter @ai-platform/web lint
pnpm --filter @ai-platform/web typecheck
pnpm --filter @ai-platform/web test
pnpm --filter @ai-platform/web build
```

涉及实际页面交互时，按 UI/UX 基线补充浏览器桌面和移动视口验收。

## 17. Git 与质量门禁

- 提交执行 [`docs/governance/delivery-and-git.md`](./delivery-and-git.md)，一个可验收节点对应一个独立提交。
- 提交信息使用 `<type>(<节点 ID>): <中文摘要>`，前端常用类型为 `feat`、`fix`、`refactor`、`test`、`style`、`docs` 和 `chore`。
- 格式化、Lint、类型、测试和构建失败时不得标记节点完成。
- 阶段 1 引入 Husky、lint-staged 和 commitlint 时，只处理本次暂存的前端文件，不修改用户未暂存或无关文件。
- 自动修复命令造成大范围无关格式变化时应撤销自动结果并缩小目标，不把格式噪音混入功能节点。

## 18. 从 digitizing 采用与排除的规则

| 分类 | 结论 | AI 平台处理方式 |
| --- | --- | --- |
| React 函数组件、Hooks、严格 TypeScript | 采用 | 按 React 19 和当前 TypeScript 版本执行 |
| 页面/API/Hook/Store/组件分层 | 采用 | 映射到 `apps/web/src/` 目标目录 |
| 公共组件优先复用和 API 保护 | 采用 | 项目组件优先，再使用 Ant Design 6 |
| 文件职责和规模预算 | 采用 | 作为评审预警，职责边界优先于机械行数 |
| ESLint、Prettier、Husky、lint-staged、commitlint | 分阶段采用 | ESLint/Prettier 已有，Git Hook 在阶段 1 单独建设 |
| axios 统一客户端 | 不采用 | 使用 Fetch Client、TanStack Query 和独立 SSE Client |
| ahooks `useRequest` | 不采用 | 服务端状态统一由 TanStack Query 管理 |
| `@seakoi/console-kit`、`@seakoi/corebox` | 不采用 | 没有当前平台依赖和领域价值 |
| Less、TailwindCSS、styled-components | 不采用 | 使用 CSS、CSS Modules 和 Ant Design Token |
| Apifox 作为接口事实源 | 不采用 | 使用仓库 OpenAPI、SSE、错误码和领域契约 |
| React 18、Router 6、Ant Design 5、Vite 5 | 不采用 | 保持当前 React 19、Router 8、Ant Design 6、Vite 8 |
| 所有导出和 Props 强制完整 JSDoc | 不采用 | 只要求公共或非直观 API 的高价值注释 |
| 未经要求禁止新增测试 | 不采用 | 测试覆盖随节点风险和安全边界增加 |

## 19. 阶段 1 落地顺序

本文档生效后，新代码立即遵守不依赖工具改造的规则。机械门禁按以下顺序建设：

1. 建立 `app/`、`routes/`、`pages/`、`components/`、`api/services/`、`styles/` 目标目录和 `@/` 路径别名。
2. 拆分阶段 0 的 `App.tsx` 和 `styles.css`，同时建设正式应用壳层，不单独做无业务价值的迁移。
3. 建立 `api/client.ts`、领域 Service、Query Key 和统一错误对象。
4. 补齐 ESLint 的 React、未使用 Import、严格相等和文件规模预警；固定项目 Prettier 配置。
5. 引入仅作用于暂存文件的 Husky、lint-staged 和 commitlint。
6. 随动态菜单、空间切换和首批业务页面验证规范，并根据真实问题修订阈值。

任何规则调整必须更新本文档、相关配置和验证报告，不能只修改说明而不更新执行门禁。
