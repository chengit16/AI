import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { collectViolations } from "./check-frontend-architecture.mjs";

function withSource(files, callback) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "frontend-architecture-"));
  try {
    for (const [relative, content] of Object.entries(files)) {
      const filePath = path.join(root, relative);
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, content);
    }
    callback(root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("允许页面依赖 API Service", () => {
  withSource(
    { "pages/list.tsx": 'import { getList } from "../api/services/list";\nvoid getList;' },
    (root) => assert.deepEqual(collectViolations(root), []),
  );
});

test("禁止公共组件依赖具体页面", () => {
  withSource(
    { "components/Button.tsx": 'import Page from "../pages/detail";\nvoid Page;' },
    (root) => {
      const violations = collectViolations(root);
      assert.equal(violations.length, 1);
      assert.match(violations[0].message, /components 层禁止依赖 pages 层/);
    },
  );
});

test("禁止公共组件动态导入具体页面", () => {
  withSource({ "components/Lazy.tsx": 'void import("../pages/detail");' }, (root) => {
    const violations = collectViolations(root);
    assert.equal(violations.length, 1);
    assert.match(violations[0].message, /components 层禁止依赖 pages 层/);
  });
});
