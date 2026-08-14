/**
 * 校验 UnoCSS 工具边界和生产构建可静态提取性。
 *
 * 检查器只处理可以确定判错的结构规则；视觉一致性、复杂 CSS 保留理由和
 * Utility 可读性仍由页面评审与多视口验收负责。
 */
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const requireFromWeb = createRequire(
  path.join(projectRoot, "apps/web/package.json"),
);
const ts = requireFromWeb("typescript");
const utilityPrefix =
  "(?:bg|text|border|rounded|grid-cols|gap|space-[xy]|[pm][trblxy]?|w|h|min-[wh]|max-[wh]|z|opacity|translate-[xy])";
const dynamicUtilityPattern = new RegExp(`${utilityPrefix}-[^\\s\\x60]*\\$\\{`);

function walk(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return walk(entryPath);
    return /\.(ts|tsx)$/.test(entry.name) ? [entryPath] : [];
  });
}

function lineOf(source, offset) {
  return source.slice(0, offset).split("\n").length;
}

/**
 * 收集 UnoCSS 确定性违规。
 *
 * @param {string} root 待检查仓库根目录
 * @returns {{ filePath: string, line: number, message: string }[]} 违规清单
 */
export function collectStyleViolations(root = projectRoot) {
  const violations = [];
  const webRoot = path.join(root, "apps/web");
  const sourceRoot = path.join(webRoot, "src");
  const configPaths = walk(webRoot).filter(
    (filePath) => path.basename(filePath) === "uno.config.ts",
  );

  if (
    configPaths.length !== 1 ||
    configPaths[0] !== path.join(webRoot, "uno.config.ts")
  ) {
    violations.push({
      filePath: webRoot,
      line: 1,
      message: "必须且只能在 apps/web/uno.config.ts 维护 UnoCSS 配置",
    });
  } else {
    const config = fs.readFileSync(configPaths[0], "utf8");
    if (!/presetWind3\s*\(\s*\{[^}]*preflight:\s*false/s.test(config)) {
      violations.push({
        filePath: configPaths[0],
        line: 1,
        message: "presetWind3 必须显式关闭 Preflight",
      });
    }
    for (const forbidden of ["presetAttributify", "presetIcons"]) {
      if (config.includes(forbidden)) {
        violations.push({
          filePath: configPaths[0],
          line: lineOf(config, config.indexOf(forbidden)),
          message: `禁止启用 ${forbidden}`,
        });
      }
    }
  }

  const vitePath = path.join(webRoot, "vite.config.ts");
  const viteSource = fs.existsSync(vitePath)
    ? fs.readFileSync(vitePath, "utf8")
    : "";
  if (
    !viteSource.includes('from "unocss/vite"') ||
    !viteSource.includes("UnoCSS()")
  ) {
    violations.push({
      filePath: vitePath,
      line: 1,
      message: "Vite 必须接入 UnoCSS 官方插件",
    });
  }

  const mainPath = path.join(sourceRoot, "main.tsx");
  const mainSource = fs.existsSync(mainPath)
    ? fs.readFileSync(mainPath, "utf8")
    : "";
  if (!mainSource.includes('import "virtual:uno.css"')) {
    violations.push({
      filePath: mainPath,
      line: 1,
      message: "浏览器入口必须装载 virtual:uno.css",
    });
  }

  for (const filePath of walk(sourceRoot)) {
    const source = fs.readFileSync(filePath, "utf8");
    const sourceFile = ts.createSourceFile(
      filePath,
      source,
      ts.ScriptTarget.Latest,
      true,
      filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    );
    function visit(node) {
      if (
        ts.isTemplateExpression(node) &&
        dynamicUtilityPattern.test(node.getText(sourceFile))
      ) {
        const { line } = sourceFile.getLineAndCharacterOfPosition(
          node.getStart(sourceFile),
        );
        violations.push({
          filePath,
          line: line + 1,
          message:
            "禁止动态拼接 UnoCSS Utility，请改用完整类名映射或最小 Safelist",
        });
      }
      ts.forEachChild(node, visit);
    }
    visit(sourceFile);
  }
  return violations;
}

function main() {
  const violations = collectStyleViolations();
  if (violations.length === 0) {
    console.log("UnoCSS 样式架构检查通过");
    return 0;
  }
  for (const violation of violations) {
    console.error(
      `${path.relative(projectRoot, violation.filePath)}:${violation.line}: ${violation.message}`,
    );
  }
  return 1;
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  process.exitCode = main();
}
