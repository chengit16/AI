import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const webRoot = path.join(projectRoot, "apps/web/src");
const requireFromWeb = createRequire(path.join(projectRoot, "apps/web/package.json"));
const ts = requireFromWeb("typescript");

const forbiddenTargets = {
  api: new Set(["pages", "components", "routes", "store"]),
  components: new Set(["pages", "routes"]),
  hooks: new Set(["pages", "routes"]),
  store: new Set(["pages", "routes", "api"]),
  types: new Set(["pages", "routes", "components", "store", "api"]),
  utils: new Set(["pages", "routes", "components", "store", "api"]),
};

function walk(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return walk(entryPath);
    return /\.(ts|tsx)$/.test(entry.name) ? [entryPath] : [];
  });
}

function sourceLayer(filePath, root) {
  const relative = path.relative(root, filePath);
  return relative.split(path.sep)[0];
}

function targetLayer(specifier, filePath, root) {
  let target;
  if (specifier.startsWith("@/")) {
    target = path.join(root, specifier.slice(2));
  } else if (specifier.startsWith(".")) {
    target = path.resolve(path.dirname(filePath), specifier);
  } else {
    return null;
  }
  const relative = path.relative(root, target);
  if (relative.startsWith("..")) return null;
  return relative.split(path.sep)[0];
}

export function collectViolations(root = webRoot) {
  const violations = [];
  for (const filePath of walk(root)) {
    const layer = sourceLayer(filePath, root);
    const forbidden = forbiddenTargets[layer];
    if (!forbidden) continue;
    const sourceText = fs.readFileSync(filePath, "utf8");
    const sourceFile = ts.createSourceFile(
      filePath,
      sourceText,
      ts.ScriptTarget.Latest,
      true,
      filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    );
    const specifiers = [];
    function visit(node) {
      if (
        (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
        node.moduleSpecifier &&
        ts.isStringLiteral(node.moduleSpecifier)
      ) {
        specifiers.push({ node, value: node.moduleSpecifier.text });
      }
      if (
        ts.isCallExpression(node) &&
        node.expression.kind === ts.SyntaxKind.ImportKeyword &&
        node.arguments.length === 1 &&
        ts.isStringLiteral(node.arguments[0])
      ) {
        specifiers.push({ node, value: node.arguments[0].text });
      }
      ts.forEachChild(node, visit);
    }
    visit(sourceFile);
    for (const specifier of specifiers) {
      const importedLayer = targetLayer(specifier.value, filePath, root);
      if (importedLayer && forbidden.has(importedLayer)) {
        const { line } = sourceFile.getLineAndCharacterOfPosition(specifier.node.getStart());
        violations.push({
          filePath,
          line: line + 1,
          message: `${layer} 层禁止依赖 ${importedLayer} 层`,
        });
      }
    }
  }
  return violations;
}

function main() {
  const violations = collectViolations();
  if (violations.length === 0) {
    console.log("React 模块依赖检查通过");
    return 0;
  }
  for (const violation of violations) {
    console.error(
      `${path.relative(projectRoot, violation.filePath)}:${violation.line}: ${violation.message}`,
    );
  }
  return 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
