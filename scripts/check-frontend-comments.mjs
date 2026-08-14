/**
 * @description 前端注释结构门禁
 *
 * 使用 TypeScript AST 校验文件头、公开导出和 Props/Options 字段；语义质量仍由
 * 代码审查判断，避免以机械注释数量替代真正的可维护性。
 */
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const requireFromWeb = createRequire(path.join(projectRoot, "apps/web/package.json"));
const ts = requireFromWeb("typescript");
const chinesePattern = /[\u3400-\u9fff]/;
const taskMarkerPattern = /\b(?:TODO|FIXME|HACK)\b/;
const validTaskMarkerPattern =
  /\b(?:TODO|FIXME|HACK)\(@[A-Za-z0-9_-]+\s+\d{4}-\d{2}\):\s+\S/;
const instructionCommentPattern =
  /(?:@ts-|eslint|prettier|istanbul|\bc8\b|vite-ignore|sourceMappingURL|@__PURE__|<reference)/i;

/** 递归收集前端手写 TypeScript 文件，不跨入依赖目录。 */
function walk(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return walk(entryPath);
    return /\.(ts|tsx)$/.test(entry.name) ? [entryPath] : [];
  });
}

/** 生成文件由契约或资源生成器维护，不能通过手工注释消除漂移。 */
function isGenerated(filePath) {
  const normalized = filePath.split(path.sep).join("/");
  return normalized.includes("/generated/") || /\.generated\.[cm]?[jt]sx?$/.test(filePath);
}

/** 纯再导出入口没有独立业务职责，由被导出模块承担接口说明。 */
function isPureReExport(sourceFile) {
  return (
    sourceFile.statements.length > 0 &&
    sourceFile.statements.every((statement) => ts.isExportDeclaration(statement))
  );
}

function lineOf(sourceFile, position) {
  return sourceFile.getLineAndCharacterOfPosition(position).line + 1;
}

/** 返回紧邻节点的 JSDoc，防止远处说明被错误复用。 */
function leadingJsDocs(sourceFile, node) {
  const trivia = sourceFile.text.slice(node.getFullStart(), node.getStart(sourceFile));
  return [...trivia.matchAll(/\/\*\*[\s\S]*?\*\//g)].map((match) => match[0]);
}

function hasExportModifier(node) {
  return Boolean(node.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword));
}

function exportedDeclarationName(node, sourceFile) {
  if (ts.isVariableStatement(node)) {
    return node.declarationList.declarations.map((item) => item.name.getText(sourceFile)).join(", ");
  }
  return node.name?.getText(sourceFile) ?? "default export";
}

function isDocumentedExport(node) {
  return (
    ts.isFunctionDeclaration(node) ||
    ts.isClassDeclaration(node) ||
    ts.isInterfaceDeclaration(node) ||
    ts.isTypeAliasDeclaration(node) ||
    ts.isEnumDeclaration(node) ||
    ts.isVariableStatement(node)
  );
}

function optionsMembers(node) {
  const name = node.name?.text ?? "";
  if (!/(?:Props|Options)$/.test(name)) return [];
  if (ts.isInterfaceDeclaration(node)) return [...node.members];
  if (ts.isTypeAliasDeclaration(node) && ts.isTypeLiteralNode(node.type)) {
    return [...node.type.members];
  }
  return [];
}

/** 使用 TypeScript Scanner 获取真实注释，避免把 URL 或字符串内容误判成行注释。 */
function sourceComments(source, scriptKind) {
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, scriptKind, source);
  const comments = [];
  for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
    if (
      token === ts.SyntaxKind.SingleLineCommentTrivia ||
      token === ts.SyntaxKind.MultiLineCommentTrivia
    ) {
      comments.push({ text: scanner.getTokenText(), position: scanner.getTokenPos() });
    }
  }
  return comments;
}

/**
 * 收集前端注释结构违规。
 *
 * @param {string} root 待检查仓库根目录
 * @returns {{ filePath: string, line: number, message: string }[]} 违规清单
 */
export function collectFrontendCommentViolations(root = projectRoot) {
  const sourceRoot = path.join(root, "apps/web/src");
  const violations = [];
  for (const filePath of walk(sourceRoot)) {
    if (isGenerated(filePath)) continue;
    const source = fs.readFileSync(filePath, "utf8");
    const scriptKind = filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sourceFile = ts.createSourceFile(
      filePath,
      source,
      ts.ScriptTarget.Latest,
      true,
      scriptKind,
    );
    if (isPureReExport(sourceFile)) continue;

    if (!/^\s*\/\*\*[\s\S]*?@description[\s\S]*?\*\//.test(source)) {
      violations.push({
        filePath,
        line: 1,
        message: "手写 TS/TSX 必须在首个 import 前提供 @description 文件头",
      });
    }

    for (const statement of sourceFile.statements) {
      if (hasExportModifier(statement) && isDocumentedExport(statement)) {
        if (leadingJsDocs(sourceFile, statement).length === 0) {
          violations.push({
            filePath,
            line: lineOf(sourceFile, statement.getStart(sourceFile)),
            message: `导出声明 ${exportedDeclarationName(statement, sourceFile)} 缺少中文 JSDoc`,
          });
        }
      }
      if (ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement)) {
        for (const member of optionsMembers(statement)) {
          if (leadingJsDocs(sourceFile, member).length === 0) {
            violations.push({
              filePath,
              line: lineOf(sourceFile, member.getStart(sourceFile)),
              message: `${statement.name.text} 字段 ${member.name?.getText(sourceFile) ?? "<unknown>"} 缺少 JSDoc`,
            });
          }
        }
      }
    }

    for (const comment of sourceComments(source, scriptKind)) {
      if (taskMarkerPattern.test(comment.text) && !validTaskMarkerPattern.test(comment.text)) {
        violations.push({
          filePath,
          line: lineOf(sourceFile, comment.position),
          message: "TODO/FIXME/HACK 必须包含责任角色、YYYY-MM 和退出条件",
        });
      }
      if (
        !instructionCommentPattern.test(comment.text) &&
        !chinesePattern.test(comment.text)
      ) {
        violations.push({
          filePath,
          line: lineOf(sourceFile, comment.position),
          message: "手写代码注释必须包含中文业务说明",
        });
      }
    }
  }
  return violations;
}

function main() {
  const violations = collectFrontendCommentViolations();
  if (violations.length === 0) {
    console.log("前端注释结构检查通过");
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
