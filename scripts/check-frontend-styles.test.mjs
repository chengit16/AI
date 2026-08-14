/** UnoCSS 门禁测试，覆盖有效配置与生产环境无法提取的动态类名。 */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { collectStyleViolations } from "./check-frontend-styles.mjs";

function withProject(files, callback) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "frontend-styles-"));
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

const validFiles = {
  "apps/web/uno.config.ts":
    'import { presetWind3 } from "unocss";\nexport default { presets: [presetWind3({ preflight: false })] };',
  "apps/web/vite.config.ts":
    'import UnoCSS from "unocss/vite";\nexport default { plugins: [UnoCSS()] };',
  "apps/web/src/main.tsx": 'import "virtual:uno.css";\n',
};

test("接受唯一配置和完整静态类名", () => {
  withProject(
    {
      ...validFiles,
      "apps/web/src/Page.tsx":
        '/** 禁止使用 `bg-${color}` 这类动态 Utility。 */\nexport const value = "bg-brand text-text";',
    },
    (root) => assert.deepEqual(collectStyleViolations(root), []),
  );
});

test("拒绝运行时拼接 Utility", () => {
  withProject(
    {
      ...validFiles,
      "apps/web/src/Page.tsx":
        "const className = `bg-${tone}`;\nvoid className;",
    },
    (root) => {
      const violations = collectStyleViolations(root);
      assert.equal(violations.length, 1);
      assert.match(violations[0].message, /禁止动态拼接 UnoCSS Utility/);
    },
  );
});

test("拒绝启用 Attributify 或图标预设", () => {
  withProject(
    {
      ...validFiles,
      "apps/web/uno.config.ts":
        'import { presetWind3, presetIcons } from "unocss";\nexport default { presets: [presetWind3({ preflight: false }), presetIcons()] };',
    },
    (root) => {
      const violations = collectStyleViolations(root);
      assert.equal(violations.length, 1);
      assert.match(violations[0].message, /禁止启用 presetIcons/);
    },
  );
});
