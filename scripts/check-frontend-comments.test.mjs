/**
 * @description 前端注释门禁反例测试
 * 覆盖文件头、导出接口、字段、任务标记、中文注释和生成文件边界。
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
