/**
 * @description 前端注释门禁反例测试
 * 覆盖文件头、导出接口、字段、长函数阶段、任务标记、中文注释和生成文件边界。
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { collectFrontendCommentViolations } from "./check-frontend-comments.mjs";

function withProject(files, callback) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "frontend-comments-"));
  try {
    for (const [relativePath, content] of Object.entries(files)) {
      const filePath = path.join(root, relativePath);
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, content);
    }
    callback(root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("接受完整文件头、导出 JSDoc 和字段说明", () => {
  withProject(
    {
      "apps/web/src/Page.tsx": `/** @description 合成页面入口 */
import type { ReactNode } from "react";
/** 合成页面属性。 */
export interface PageProps {
  /** 页面正文。 */
  children: ReactNode;
}
/** 渲染合成页面。 */
export function Page({ children }: PageProps) { return children; }
`,
    },
    (root) => assert.deepEqual(collectFrontendCommentViolations(root), []),
  );
});

test("拒绝缺少文件头或导出说明", () => {
  withProject(
    { "apps/web/src/service.ts": "export function load() { return true; }\n" },
    (root) => {
      const messages = collectFrontendCommentViolations(root).map(({ message }) => message);
      assert.ok(messages.some((message) => message.includes("@description")));
      assert.ok(messages.some((message) => message.includes("导出声明")));
    },
  );
});

test("拒绝未说明的 Props 和 Options 字段", () => {
  withProject(
    {
      "apps/web/src/types.ts": `/** @description 合成类型 */
/** 合成选项。 */
export interface LoadOptions { signal?: AbortSignal; }
`,
    },
    (root) => {
      const violations = collectFrontendCommentViolations(root);
      assert.equal(violations.length, 1);
      assert.match(violations[0].message, /字段 signal/);
    },
  );
});

test("拒绝无责任信息任务标记和纯英文业务注释", () => {
  withProject(
    {
      "apps/web/src/task.ts": `/** @description 合成任务 */
// TODO: later
// business fallback
const value = true;
void value;
`,
    },
    (root) => {
      const messages = collectFrontendCommentViolations(root).map(({ message }) => message);
      assert.ok(messages.some((message) => message.includes("责任角色")));
      assert.ok(messages.some((message) => message.includes("中文业务说明")));
    },
  );
});

test("忽略生成文件和纯再导出入口", () => {
  withProject(
    {
      "apps/web/src/api/generated/client.ts": "// generated client\nexport type Value = string;\n",
      "apps/web/src/resource.generated.ts": "// generated registry\nexport const value = true;\n",
      "apps/web/src/index.ts": 'export { value } from "./value";\n',
    },
    (root) => assert.deepEqual(collectFrontendCommentViolations(root), []),
  );
});

test("拒绝缺少阶段说明的 30 行多阶段函数", () => {
  const assignments = Array.from({ length: 28 }, (_, index) => `  const value${index} = ${index};`).join(
    "\n",
  );
  withProject(
    {
      "apps/web/src/workflow.ts": `/** @description 合成长流程 */
function workflow(enabled: boolean) {
${assignments}
  if (enabled) console.log(value0);
  if (!enabled) console.log(value1);
  return value27;
}
void workflow;
`,
    },
    (root) => {
      const messages = collectFrontendCommentViolations(root).map(({ message }) => message);
      assert.ok(messages.some((message) => message.includes("至少需要 2 个")));
    },
  );
});

test("接受具有连续编号说明的 30 行多阶段函数", () => {
  const assignments = Array.from({ length: 28 }, (_, index) => `  const value${index} = ${index};`).join(
    "\n",
  );
  withProject(
    {
      "apps/web/src/workflow.ts": `/** @description 合成长流程 */
function workflow(enabled: boolean) {
  // 1. 准备后续分支共同使用的合成事实。
${assignments}
  // 2. 按输入状态进入互斥业务分支。
  if (enabled) console.log(value0);
  if (!enabled) console.log(value1);
  return value27;
}
void workflow;
`,
    },
    (root) => assert.deepEqual(collectFrontendCommentViolations(root), []),
  );
});

test("拒绝 60 行函数缺少三个阶段或编号断裂", () => {
  const assignments = Array.from({ length: 61 }, (_, index) => `  const value${index} = ${index};`).join(
    "\n",
  );
  withProject(
    {
      "apps/web/src/workflow.ts": `/** @description 合成强制分段流程 */
function workflow() {
  // 1. 建立第一组合成事实。
${assignments}
  // 3. 返回最终合成结果。
  return value60;
}
void workflow;
`,
    },
    (root) => {
      const messages = collectFrontendCommentViolations(root).map(({ message }) => message);
      assert.ok(messages.some((message) => message.includes("编号必须从 1 连续递增")));
      assert.ok(messages.some((message) => message.includes("至少需要 3 个")));
    },
  );
});

test("接受具有三个连续编号阶段的 60 行函数", () => {
  const assignments = Array.from({ length: 61 }, (_, index) => {
    const comments = {
      0: "  // 1. 建立第一组合成事实。\n",
      20: "  // 2. 建立第二组合成事实。\n",
      40: "  // 3. 建立第三组合成事实。\n",
    };
    return `${comments[index] ?? ""}  const value${index} = ${index};`;
  }).join("\n");
  withProject(
    {
      "apps/web/src/workflow.ts": `/** @description 合成强制分段流程 */
function workflow() {
${assignments}
  return value60;
}
void workflow;
`,
    },
    (root) => assert.deepEqual(collectFrontendCommentViolations(root), []),
  );
});

test("拒绝 100 行函数未说明保留原因", () => {
  const assignments = Array.from({ length: 101 }, (_, index) => `  const value${index} = ${index};`).join(
    "\n",
  );
  withProject(
    {
      "apps/web/src/workflow.ts": `/** @description 合成超长流程 */
function workflow() {
  // 1. 建立第一组合成事实。
${assignments}
  // 2. 保持合成流程的中间检查点。
  console.log(value50);
  // 3. 返回最终合成结果。
  return value100;
}
void workflow;
`,
    },
    (root) => {
      const messages = collectFrontendCommentViolations(root).map(({ message }) => message);
      assert.ok(messages.some((message) => message.includes("长函数保留原因")));
    },
  );
});

test("接受具有保留原因及完整阶段的 100 行函数", () => {
  const assignments = Array.from({ length: 101 }, (_, index) => {
    const comments = {
      0: "  // 1. 建立第一组合成事实。\n",
      35: "  // 2. 建立第二组合成事实。\n",
      70: "  // 3. 建立第三组合成事实。\n",
    };
    return `${comments[index] ?? ""}  const value${index} = ${index};`;
  }).join("\n");
  withProject(
    {
      "apps/web/src/workflow.ts": `/** @description 合成超长流程 */
function workflow() {
  // 长函数保留原因: 合成测试必须在同一作用域覆盖超长函数验收边界。
${assignments}
  return value100;
}
void workflow;
`,
    },
    (root) => assert.deepEqual(collectFrontendCommentViolations(root), []),
  );
});

test("嵌套函数独立检查且不重复计入外层", () => {
  const assignments = Array.from({ length: 61 }, (_, index) => `    const value${index} = ${index};`).join(
    "\n",
  );
  withProject(
    {
      "apps/web/src/nested.ts": `/** @description 合成嵌套流程 */
function outer() {
  return function inner() {
${assignments}
    return value60;
  };
}
void outer;
`,
    },
    (root) => {
      const violations = collectFrontendCommentViolations(root);
      assert.equal(violations.length, 1);
      assert.match(violations[0].message, /长函数 inner.*至少需要 3 个/);
    },
  );
});

test("纯 JSX 标签排版不计入长函数有效代码", () => {
  const rows = Array.from({ length: 120 }, (_, index) => `      <span>合成行 ${index}</span>`).join(
    "\n",
  );
  withProject(
    {
      "apps/web/src/View.tsx": `/** @description 合成声明式页面 */
function View() {
  return (
    <section>
${rows}
    </section>
  );
}
void View;
`,
    },
    (root) => assert.deepEqual(collectFrontendCommentViolations(root), []),
  );
});
