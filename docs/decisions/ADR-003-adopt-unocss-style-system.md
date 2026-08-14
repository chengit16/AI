# ADR-003：采用 UnoCSS 主样式体系并渐进迁移 CSS Modules

## 状态

`已接受`

## 日期

2026-08-14

## 背景

阶段 1 已形成 React 19、Vite 8、Ant Design 6 前端壳层和首批业务页面。当前 `apps/web/src` 有 12 个 CSS Module，连同全局样式和语义 Token 共约 1064 行；18 个 TSX 文件引用 CSS Module，复杂度主要集中在应用壳层、登录、知识生产、工作空间概览和模型治理页面。

继续为每个页面维护独立 CSS Module 可以保持局部作用域，但会让常用布局、间距、尺寸和响应式规则在页面间重复。当前页面数量仍有限，是调整样式主路径且避免后续迁移成本继续增长的合适窗口。

平台必须继续保持以下不变量：Ant Design 组件主题由 `ConfigProvider` 管理；颜色、间距、圆角、层级和动效使用语义 Token；移动端、窄屏、横屏和可访问性基线不降低；样式工具不能改变菜单权限、接口授权或业务状态；迁移不得与业务功能节点混成不可回滚的大提交。

## 决策驱动因素

- 减少页面级 CSS 文件和重复的布局、间距、尺寸及响应式声明。
- 新增问答、工作流和审批页面前固定一致、可复用的样式表达方式。
- 保持 Ant Design 主题、现有 CSS Variables 和复杂兼容规则的稳定边界。
- 避免纯 Utility 写法导致 JSX 过长、动态类名漏生成或复杂选择器难以维护。
- 迁移必须可以按页面回滚，并通过现有桌面、移动、横屏和可访问性门禁。

## 备选方案

### 方案 A：继续以 CSS Modules 为主

无需修改工具链，当前页面行为风险最低，但新增页面仍会继续产生页面 CSS 文件和重复样式，后续迁移成本随页面数量增长。

### 方案 B：UnoCSS 为主，保留必要语义 CSS

普通布局、间距、尺寸、排版、颜色和响应式使用 UnoCSS；保留 `tokens.css`、`global.css`、Ant Design 覆盖以及复杂状态/选择器 CSS。该方案需要迁移和视觉回归，但能在减少样式文件的同时保留清晰的复杂样式边界。

### 方案 C：纯 UnoCSS 并删除全部 CSS 文件

形式最统一，但会迫使全局 Reset、`prefers-reduced-motion`、Ant Design 内部覆盖、复合媒体查询和复杂父子状态进入冗长 Utility 或自定义 Variant，降低可读性并扩大回归风险。

## 决策

采用方案 B，并固定以下边界：

1. UnoCSS 成为 Web 前端新增和迁移页面的样式主路径，使用官方 Vite 插件和与当前版本兼容的 Wind 预设；具体依赖版本在 `P1S-01` 安装时固定并进入锁文件。
2. 默认关闭 UnoCSS Preflight，继续由 `styles/global.css` 管理 Reset、基础排版、触控目标、焦点和减少动效规则，避免与 Ant Design 产生全局覆盖冲突。
3. `styles/tokens.css` 继续作为 CSS 语义变量来源；UnoCSS Theme 和 Shortcuts 只能引用语义变量，业务 JSX 不散落重复十六进制颜色。Ant Design Token 继续由 `ConfigProvider` 管理，并在样式治理节点中核对重复值。
4. 普通布局、间距、尺寸、排版、颜色、边框和单一断点响应式优先使用 UnoCSS。Ant Design 内部选择器、复杂父子状态、复合宽高媒体查询、全局可访问性和难以清晰表达的交互状态允许保留少量 CSS。
5. React 统一通过 `className` 使用 Utility，不启用 Attributify，不引入 UnoCSS 图标预设；图标继续使用 `lucide-react`。
6. 动态状态使用完整类名映射或受控 Safelist，禁止 `bg-${color}` 等无法被静态扫描可靠识别的拼接。条件类名通过项目统一 `cn()` 工具组合。
7. 高频且稳定的视觉模式可以定义 UnoCSS Shortcut，但 Shortcut 必须表达明确语义、引用项目 Token，不能演变为隐藏任意样式的大型页面类。
8. 迁移期间允许 UnoCSS 与 CSS Modules 共存。每个 CSS Module 只有在对应页面完成功能、响应式和视觉验收后才能删除；不为了追求零 CSS 文件牺牲可读性。

目标是让 UnoCSS 承担约 80% 的常规页面样式，保留约 20% 的全局、Ant Design 覆盖、复杂选择器和兼容 CSS。该比例用于指导评审，不作为按行数机械删除样式的指标。

## 影响

### 正面影响

- 新页面不再默认创建独立 CSS Module，常用样式能直接复用统一 Token 和断点。
- 页面间距、尺寸和响应式表达更一致，减少同类规则复制和命名成本。
- 当前仅 12 个 CSS Module，渐进迁移和逐页回滚仍然可控。
- UnoCSS 按实际使用生成样式，生产构建不会引入完整预设样式表。

### 负面影响与风险

- 18 个现有 TSX 文件和 12 个 CSS Module 需要逐步迁移，复杂页面必须重新做视觉验收。
- Utility 较多时 JSX 可读性下降，需要通过组件拆分、`cn()` 和有限 Shortcut 控制。
- 动态拼接类名可能在生产构建中缺失，必须使用显式映射和构建验收。
- 当前 `560/600/720/820/1024px` 及横屏复合媒体查询不能直接替换为默认断点。
- UnoCSS、CSS Variables 和 Ant Design Token 可能形成重复配置，必须保持语义 Token 单一来源。

## 验证指标

- UnoCSS 与 Node.js 24、Vite 8、React 19、TypeScript 和生产构建组合通过固定版本验证。
- Preflight 关闭后，Ant Design Button、Modal、Drawer、Table、Form 和 Input 的尺寸、主题及交互状态无回归。
- 生产构建中所有静态类名、条件类名和 Safelist 样式均存在，不出现仅开发环境可见的样式。
- 每个迁移页面通过 `375×812`、`390×844`、`768×1024`、`1024×768`、`1440×900` 和横屏视口检查。
- 无新增横向溢出、布局跳动、文本遮挡或焦点丢失；`prefers-reduced-motion` 保持可用。
- 完成迁移后删除无引用 CSS Module，并记录仍保留的复杂 CSS 及原因。

## 迁移与回滚

1. `P1S-01` 接入 UnoCSS、固定 Token/断点/Shortcut 和生产扫描门禁，不迁移业务页面。
2. `P1S-02` 迁移公共组件与应用壳层，验证导航、折叠和移动抽屉。
3. `P1S-03` 迁移登录、状态、概览、成员和组织等普通页面。
4. `P1S-04` 迁移知识生产和模型治理复杂页面，保留必要复杂 CSS。
5. `P1S-05` 清理无引用 CSS Module，执行完整响应式、可访问性和生产构建验收。

迁移过程中旧 CSS Module 在页面验收前保持可恢复；单页失败时回滚该页 TSX 和对应 CSS，不需要回滚其他已通过页面。若 UnoCSS 与 Vite 8 或 Ant Design 6 存在无法隔离的冲突，则保留 Token 和规范改进，撤销 UnoCSS 插件与虚拟样式入口，恢复 CSS Modules 主路径。本决策不涉及数据、接口或 Migration，不存在不可逆业务操作。

## 后续事项

- `P1S-01` 固定依赖版本、`uno.config.ts`、`cn()`、断点、Safelist 和 Preflight 策略。
- `P1S-05` 已完成：CSS Module 从 12 个归零，仅保留职责明确的 `tokens.css` 与 `global.css` 共 107 行；生产构建和全页面多视口验收通过，完成态门禁禁止重新引入 CSS Module、无消费者 Token 和无显式样式的边框宽度 Utility。
- `P1E-06` 问答页面和 `P1F-05` 工作流审批页面必须在 `P1S-05` 后按 UnoCSS 主路径建设。
- 主色调、字体和暗色模式仍在 `P1G-04` 统一评审，不与本次工具迁移混合决定。

## 关联内容

- [Web 前端代码规范](../governance/frontend-code-standards.md)
- [前端 UI/UX 设计基线](../design/ui-ux-baseline.md)
- [阶段 1 实施计划](../stages/stage-1-plan.md)
- [架构决策管理规范](../governance/architecture-decisions.md)
