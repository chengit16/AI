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
const stageCommentPattern = /(?:\/\/|\/\*)\s*(\d+)[.、．]\s*(\S[\s\S]*?)(?:\*\/)?$/;
const longFunctionReasonPattern = /(?:\/\/|\/\*)\s*长函数保留原因[：:]\s*\S/;
const longFunctionReviewLines = 30;
const longFunctionRequiredLines = 60;
const longFunctionSplitLines = 100;
const layoutOnlyTokenPattern = /^[()[\]{},.:;]+$/;
const stageCallNames = new Set([
  "execute",
  "fetch",
  "invalidateQueries",
  "mutate",
  "publish",
  "refetch",
  "request",
  "send",
  "useMutation",
  "useQuery",
]);

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

/** 返回真实代码 Token，后续按函数体范围统计有效代码行。 */
function sourceCodeTokens(source, scriptKind) {
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, scriptKind, source);
  const tokens = [];
  const ignoredKinds = new Set([
    ts.SyntaxKind.WhitespaceTrivia,
    ts.SyntaxKind.NewLineTrivia,
    ts.SyntaxKind.SingleLineCommentTrivia,
    ts.SyntaxKind.MultiLineCommentTrivia,
    ts.SyntaxKind.EndOfFileToken,
  ]);
  for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
    if (!ignoredKinds.has(token)) {
      tokens.push({
        kind: token,
        text: scanner.getTokenText(),
        position: scanner.getTokenPos(),
        end: scanner.getTextPos(),
      });
    }
  }
  return tokens;
}

function isJsxNode(node) {
  while (ts.isParenthesizedExpression(node)) node = node.expression;
  return (
    ts.isJsxElement(node) ||
    ts.isJsxSelfClosingElement(node) ||
    ts.isJsxFragment(node)
  );
}

function functionName(node, sourceFile) {
  if (node.name) return node.name.getText(sourceFile);
  const parent = node.parent;
  if (ts.isVariableDeclaration(parent) && parent.name) return parent.name.getText(sourceFile);
  if (ts.isPropertyAssignment(parent) || ts.isMethodDeclaration(parent)) {
    return parent.name?.getText(sourceFile) ?? "<callback>";
  }
  return `<callback@${lineOf(sourceFile, node.getStart(sourceFile))}>`;
}

/** 遍历当前函数直接拥有的 AST，嵌套回调由自己的函数检查承担。 */
function visitOwnedNodes(node, callback) {
  function visit(current) {
    ts.forEachChild(current, (child) => {
      if (child !== node && ts.isFunctionLike(child)) return;
      callback(child);
      visit(child);
    });
  }
  visit(node);
}

function nestedFunctionRanges(node) {
  const ranges = [];
  function visit(current) {
    ts.forEachChild(current, (child) => {
      if (child !== node && ts.isFunctionLike(child)) {
        ranges.push([child.getStart(), child.end]);
        return;
      }
      visit(child);
    });
  }
  visit(node);
  return ranges;
}

function positionInRanges(position, ranges) {
  return ranges.some(([start, end]) => start <= position && position <= end);
}

/** 纯 JSX 返回和函数内类型声明不计入逻辑长度，但其中嵌套回调仍单独检查。 */
function declarativeRanges(node) {
  const ranges = [];
  visitOwnedNodes(node, (child) => {
    if (ts.isReturnStatement(child) && child.expression && isJsxNode(child.expression)) {
      ranges.push([child.expression.getStart(), child.expression.end]);
    }
    if (ts.isTypeAliasDeclaration(child) || ts.isInterfaceDeclaration(child)) {
      ranges.push([child.getStart(), child.end]);
    }
  });
  if (!ts.isBlock(node.body) && isJsxNode(node.body)) {
    ranges.push([node.body.getStart(), node.body.end]);
  }
  return ranges;
}

function effectiveFunctionLines(sourceFile, node, codeTokens) {
  const bodyStart = node.body.getStart(sourceFile);
  const bodyEnd = node.body.end;
  const excludedRanges = [...nestedFunctionRanges(node), ...declarativeRanges(node)];
  const lines = new Set();
  for (const token of codeTokens) {
    if (token.position < bodyStart || token.end > bodyEnd) continue;
    if (positionInRanges(token.position, excludedRanges)) continue;
    if (layoutOnlyTokenPattern.test(token.text)) continue;
    lines.add(lineOf(sourceFile, token.position));
  }
  return lines.size;
}

function callName(node) {
  if (!ts.isCallExpression(node)) return "";
  const expression = node.expression;
  if (ts.isIdentifier(expression)) return expression.text;
  if (ts.isPropertyAccessExpression(expression)) return expression.name.text;
  return "";
}

function stageSignalCount(node) {
  let count = 0;
  visitOwnedNodes(node, (child) => {
    if (
      ts.isIfStatement(child) ||
      ts.isForStatement(child) ||
      ts.isForInStatement(child) ||
      ts.isForOfStatement(child) ||
      ts.isWhileStatement(child) ||
      ts.isDoStatement(child) ||
      ts.isSwitchStatement(child) ||
      ts.isTryStatement(child) ||
      ts.isAwaitExpression(child)
    ) {
      count += 1;
    }
    if (ts.isCallExpression(child) && stageCallNames.has(callName(child))) {
      count += 1;
    }
  });
  return count;
}

/** 检查长函数内部阶段说明，语义质量仍由代码审查负责。 */
function checkLongFunctions(sourceFile, source, scriptKind, violations) {
  const codeTokens = sourceCodeTokens(source, scriptKind);
  const comments = sourceComments(source, scriptKind);

  function visit(node) {
    if (ts.isFunctionLike(node) && node.body) {
      const effectiveLines = effectiveFunctionLines(sourceFile, node, codeTokens);
      if (effectiveLines >= longFunctionReviewLines) {
        const nestedRanges = nestedFunctionRanges(node);
        const bodyStart = node.body.getStart(sourceFile);
        const bodyEnd = node.body.end;
        const ownedComments = comments.filter(
          (comment) =>
            bodyStart <= comment.position &&
            comment.position <= bodyEnd &&
            !positionInRanges(comment.position, nestedRanges),
        );
        const numbers = ownedComments
          .map((comment) => ({
            line: lineOf(sourceFile, comment.position),
            match: comment.text.match(stageCommentPattern),
          }))
          .filter(({ match }) => match)
          .sort((left, right) => left.line - right.line)
          .map(({ match }) => Number(match[1]));
        const expected = Array.from({ length: numbers.length }, (_, index) => index + 1);
        const name = functionName(node, sourceFile);
        if (numbers.length && numbers.some((number, index) => number !== expected[index])) {
          violations.push({
            filePath: sourceFile.fileName,
            line: lineOf(sourceFile, node.getStart(sourceFile)),
            message: `长函数 ${name} 的阶段注释编号必须从 1 连续递增`,
          });
        }

        const requiredStages =
          effectiveLines >= longFunctionRequiredLines ? 3 : stageSignalCount(node) >= 2 ? 2 : 0;
        if (requiredStages && numbers.length < requiredStages) {
          violations.push({
            filePath: sourceFile.fileName,
            line: lineOf(sourceFile, node.getStart(sourceFile)),
            message: `长函数 ${name} 有 ${effectiveLines} 行有效代码，至少需要 ${requiredStages} 个连续编号的内部阶段注释`,
          });
        }
        if (
          effectiveLines >= longFunctionSplitLines &&
          !ownedComments.some((comment) => longFunctionReasonPattern.test(comment.text))
        ) {
          violations.push({
            filePath: sourceFile.fileName,
            line: lineOf(sourceFile, node.getStart(sourceFile)),
            message: `长函数 ${name} 有 ${effectiveLines} 行有效代码，必须拆分或使用“长函数保留原因：”说明取舍`,
          });
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
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

    checkLongFunctions(sourceFile, source, scriptKind, violations);

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
