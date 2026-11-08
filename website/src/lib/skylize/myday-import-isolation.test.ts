// @vitest-environment node
//
// The security property of design section 5.0.3, enforced BY CONSTRUCTION and
// re-checked automatically on every run.
//
// Claim: no module reachable from the `/api/my-day/*` route entrypoints can
// transitively reach the service key. The only module that ever attaches the
// shared service credential is `client.ts` (as `X-API-Key`, client.ts:142-151),
// and the only getter that returns it is `getBackendConfig` (config.ts:63-85).
// If My Day never imports those, the credential is unreachable and the
// fail-closed fallback is impossible rather than merely discouraged.
//
// HOW THE WALK WORKS (P03(e))
// ---------------------------
// 1. Entrypoints are DISCOVERED, not listed: every `route.ts` / `route.tsx`
//    under app/api/my-day/** (recursive fs walk). The discovery must find at
//    least the routes that exist today, so an empty glob cannot pass vacuously.
// 2. Each file is parsed with the TypeScript compiler API (`typescript` is a
//    direct devDependency, website/package.json) — not regex — and EVERY module
//    reference is extracted: `import ... from`, `import type`, bare `import "x"`,
//    `export ... from` / `export * from`, dynamic `import("x")`, `require("x")`
//    (and `module.require` / `require.resolve`), TS `import x = require("x")`,
//    type-position `import("x")`, `/// <reference path>`, and no-substitution
//    template specifiers (`import(\`x\`)`). A specifier that is NOT a literal
//    (a template with `${...}`, a variable, a concatenation) or a `require`
//    handed around as a value is UNANALYZABLE and FAILS the test.
// 3. Relative and `@/` specifiers resolve to concrete files and are walked
//    transitively; bare package specifiers (next/server, zod, ...) are leaves.
// The test then asserts:
//   a. No file in the graph is a forbidden Console / service-key module
//      (client.ts, handler.ts, proxy-gate.ts, session.ts, config.ts).
//   b. No file in the graph contains `X-API-Key` or `SKYLIZE_SERVICE_API_KEY`
//      in CODE (comments excluded), compared CASE-INSENSITIVELY.

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

// This test file lives at website/src/lib/skylize/. Anchor everything from the
// `website/src` root so the walk is independent of the process cwd.
const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(HERE, "..", ".."); // website/src
const LIB_SKYLIZE = resolve(SRC_ROOT, "lib", "skylize");
const MYDAY_API_ROOT = resolve(SRC_ROOT, "app", "api", "my-day");

// ── entrypoint discovery (glob over app/api/my-day/**/route.{ts,tsx}) ───────

const ROUTE_FILE = /^route\.tsx?$/;

function discoverRouteEntrypoints(dir: string): string[] {
  if (!existsSync(dir)) return [];
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = resolve(dir, entry.name);
    if (entry.isDirectory()) found.push(...discoverRouteEntrypoints(full));
    else if (entry.isFile() && ROUTE_FILE.test(entry.name)) found.push(full);
  }
  return found.sort();
}

const ENTRYPOINTS = discoverRouteEntrypoints(MYDAY_API_ROOT);

// Floor for the discovery: the routes that exist today. The glob may find MORE
// (a new route is walked automatically) but never fewer.
const KNOWN_ROUTES = [
  resolve(MYDAY_API_ROOT, "session", "route.ts"),
  resolve(MYDAY_API_ROOT, "me", "route.ts"),
];

// ── forbidden modules and literals (semantics unchanged) ────────────────────

// Importing (even transitively) any of these violates the isolation guarantee.
const FORBIDDEN_FILES = new Set(
  [
    "client.ts", // holds skylizeFetch + the X-API-Key attachment
    "handler.ts", // Console's route helper (imports client.ts + getConsoleAuthConfig)
    "proxy-gate.ts", // Console's edge gate (imports session.ts)
    "session.ts", // Console's signing-only cookie
    "config.ts", // exports getBackendConfig — the only getter returning serviceApiKey
  ].map((name) => resolve(LIB_SKYLIZE, name)),
);

// Literal strings that would signal a service-key code path if found in any
// reachable My Day source file. Checked against CODE only (comments removed by
// the TS parser's own comment ranges), case-insensitively (N4): `x-api-key` is
// the same HTTP header as `X-API-Key`. The My Day modules deliberately NAME
// these in doc comments to document the prohibition; that prose is not code.
const FORBIDDEN_LITERALS = ["X-API-Key", "SKYLIZE_SERVICE_API_KEY"];

// ── TypeScript-AST import extraction ────────────────────────────────────────

type ImportForm =
  | "import"
  | "import type"
  | "import (bare)"
  | "export from"
  | "dynamic import()"
  | "require()"
  | "import = require()"
  | "type import()"
  | "triple-slash reference";

interface FoundSpecifier {
  spec: string;
  form: ImportForm;
  line: number;
}

interface Unanalyzable {
  form: string;
  line: number;
  text: string;
}

interface ImportScan {
  specifiers: FoundSpecifier[];
  unanalyzable: Unanalyzable[];
}

function parse(fileName: string, source: string): ts.SourceFile {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, kind);
}

/** The literal text of a specifier node, or null when it is not a plain literal. */
function literalText(node: ts.Node | undefined): string | null {
  if (node === undefined) return null;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    return node.text;
  }
  return null;
}

/** `require(...)`, `module.require(...)`, `require.resolve(...)` callees. */
function isRequireLikeCallee(callee: ts.Expression): boolean {
  if (ts.isIdentifier(callee)) return callee.text === "require";
  if (ts.isPropertyAccessExpression(callee)) {
    const left = callee.expression;
    if (callee.name.text === "require") return true; // module.require / x.require
    if (ts.isIdentifier(left) && left.text === "require") return true; // require.resolve
  }
  return false;
}

/** True when an Identifier `require` sits in a name (not value) position. */
function isNamePosition(node: ts.Identifier): boolean {
  const parent = node.parent;
  if (ts.isPropertyAccessExpression(parent) && parent.name === node) return true;
  if (
    (ts.isPropertyAssignment(parent) ||
      ts.isPropertyDeclaration(parent) ||
      ts.isPropertySignature(parent) ||
      ts.isMethodDeclaration(parent) ||
      ts.isMethodSignature(parent) ||
      ts.isGetAccessorDeclaration(parent) ||
      ts.isSetAccessorDeclaration(parent) ||
      ts.isEnumMember(parent)) &&
    parent.name === node
  ) {
    return true;
  }
  if (ts.isQualifiedName(parent) && parent.right === node) return true;
  return false;
}

/** Every module reference in `source`, plus every reference it cannot analyse. */
function scanImports(fileName: string, source: string): ImportScan {
  const sf = parse(fileName, source);
  const specifiers: FoundSpecifier[] = [];
  const unanalyzable: Unanalyzable[] = [];
  const lineOf = (node: ts.Node) =>
    sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;

  const record = (form: ImportForm, specNode: ts.Node | undefined, at: ts.Node) => {
    const spec = literalText(specNode);
    if (spec === null) {
      unanalyzable.push({ form, line: lineOf(at), text: at.getText(sf).slice(0, 120) });
    } else {
      specifiers.push({ spec, form, line: lineOf(at) });
    }
  };

  // `/// <reference path="..." />` pulls a file into the program too.
  for (const ref of sf.referencedFiles) {
    specifiers.push({
      spec: ref.fileName.startsWith(".") ? ref.fileName : `./${ref.fileName}`,
      form: "triple-slash reference",
      line: sf.getLineAndCharacterOfPosition(ref.pos).line + 1,
    });
  }

  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node)) {
      // Static `import ... from`, `import type ... from`, and bare `import "x"`.
      const clause = node.importClause;
      const form: ImportForm =
        clause === undefined ? "import (bare)" : clause.isTypeOnly ? "import type" : "import";
      record(form, node.moduleSpecifier, node);
    } else if (ts.isExportDeclaration(node)) {
      // `export { a } from "x"`, `export * from "x"`, `export type { T } from "x"`.
      if (node.moduleSpecifier !== undefined) record("export from", node.moduleSpecifier, node);
    } else if (ts.isImportEqualsDeclaration(node)) {
      // TS `import x = require("x")` (an `import x = A.B` alias is not a module ref).
      if (ts.isExternalModuleReference(node.moduleReference)) {
        record("import = require()", node.moduleReference.expression, node);
      }
    } else if (ts.isCallExpression(node)) {
      if (node.expression.kind === ts.SyntaxKind.ImportKeyword) {
        // Dynamic `import("x")` / `import(\`x\`)`; anything computed is unanalyzable.
        record("dynamic import()", node.arguments[0], node);
      } else if (isRequireLikeCallee(node.expression)) {
        record("require()", node.arguments[0], node);
      }
    } else if (ts.isImportTypeNode(node)) {
      // Type-position `typeof import("x")` / `import("x").T`.
      const arg = node.argument;
      record("type import()", ts.isLiteralTypeNode(arg) ? arg.literal : undefined, node);
    } else if (ts.isIdentifier(node)) {
      // `require` / `createRequire` used as a VALUE (aliased, passed around)
      // defeats static analysis: `const r = require; r(name)`.
      const text = node.text;
      if ((text === "require" || text === "createRequire") && !isNamePosition(node)) {
        const parent = node.parent;
        const isDirectCallee =
          text === "require" && ts.isCallExpression(parent) && parent.expression === node;
        const isRequireDotX =
          text === "require" &&
          ts.isPropertyAccessExpression(parent) &&
          parent.expression === node &&
          ts.isCallExpression(parent.parent) &&
          parent.parent.expression === parent;
        if (!isDirectCallee && !isRequireDotX) {
          unanalyzable.push({
            form: `${text} used as a value`,
            line: lineOf(node),
            text: parent.getText(sf).slice(0, 120),
          });
        }
      }
    } else if (
      ts.isPropertyAccessExpression(node) &&
      node.name.text === "require" &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "module" &&
      !(ts.isCallExpression(node.parent) && node.parent.expression === node)
    ) {
      // `const r = module.require` — same evasion as aliasing `require`.
      unanalyzable.push({
        form: "module.require used as a value",
        line: lineOf(node),
        text: node.getText(sf).slice(0, 120),
      });
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return { specifiers, unanalyzable };
}

/**
 * The source with every comment blanked out, using the comment ranges the TS
 * scanner itself attributes to each token's trivia. Unlike a regex strip, this
 * never treats `//` inside a string or template literal as a comment, so it
 * cannot hide a real code reference.
 */
function codeWithoutComments(fileName: string, source: string): string {
  const sf = parse(fileName, source);
  const ranges: Array<[number, number]> = [];
  const scanned = new Set<number>();
  const collect = (pos: number) => {
    if (scanned.has(pos)) return;
    scanned.add(pos);
    ts.forEachLeadingCommentRange(source, pos, (start, end) => {
      ranges.push([start, end]);
    });
    ts.forEachTrailingCommentRange(source, pos, (start, end) => {
      ranges.push([start, end]);
    });
  };
  const visit = (node: ts.Node): void => {
    // JSDoc nodes live INSIDE a comment; their range is already collected from
    // the owning token's leading trivia, and re-scanning from inside it could
    // misread comment text as code boundaries.
    if (node.kind >= ts.SyntaxKind.FirstJSDocNode && node.kind <= ts.SyntaxKind.LastJSDocNode) {
      return;
    }
    collect(node.pos);
    collect(node.end);
    for (const child of node.getChildren(sf)) visit(child);
  };
  visit(sf);

  const chars = source.split("");
  for (const [start, end] of ranges) {
    for (let i = start; i < end; i += 1) {
      if (chars[i] !== "\n" && chars[i] !== "\r") chars[i] = " ";
    }
  }
  return chars.join("");
}

function forbiddenLiteralHits(fileName: string, source: string): string[] {
  const code = codeWithoutComments(fileName, source).toLowerCase();
  return FORBIDDEN_LITERALS.filter((literal) => code.includes(literal.toLowerCase()));
}

// ── resolution and the transitive walk ──────────────────────────────────────

/**
 * Resolve a first-party import specifier (relative, or the `@/` alias) to a
 * concrete file on disk. Returns null for bare package specifiers
 * (next/server, zod, node:*, react, ...) — those are leaves.
 */
function resolveFirstParty(spec: string, fromFile: string): string | null {
  let base: string | null = null;
  if (spec.startsWith(".")) {
    base = resolve(dirname(fromFile), spec);
  } else if (spec.startsWith("@/")) {
    base = resolve(SRC_ROOT, spec.slice(2));
  } else {
    return null; // bare package — not first-party
  }
  const stem = base.replace(/\.(?:js|jsx|mjs)$/, ""); // bundler maps ./x.js -> x.ts
  const candidates = [
    base,
    `${stem}.ts`,
    `${stem}.tsx`,
    resolve(base, "index.ts"),
    resolve(base, "index.tsx"),
  ];
  for (const c of candidates) {
    if (existsSync(c) && /\.tsx?$/.test(c)) return c;
  }
  // A first-party spec that does not resolve is itself suspicious; surface it as
  // the resolved-but-missing path so a broken import can't silently pass.
  return `${stem}.ts`;
}

interface Graph {
  files: Set<string>;
  edges: Array<{ from: string; to: string; form: ImportForm }>;
  unanalyzable: string[];
}

/** Transitive first-party import graph reachable from `roots`. */
function reachableGraph(roots: string[]): Graph {
  const visited = new Set<string>();
  const edges: Graph["edges"] = [];
  const unanalyzable: string[] = [];
  const stack = [...roots];

  while (stack.length > 0) {
    const file = stack.pop() as string;
    if (visited.has(file)) continue;
    visited.add(file);

    // A missing file is surfaced by the dangling-hop test, not skipped silently.
    if (!existsSync(file)) continue;
    const scan = scanImports(file, readFileSync(file, "utf8"));
    for (const u of scan.unanalyzable) {
      unanalyzable.push(`${file}:${u.line} [${u.form}] ${u.text}`);
    }
    for (const { spec, form } of scan.specifiers) {
      const target = resolveFirstParty(spec, file);
      if (target === null) continue; // bare package leaf
      edges.push({ from: file, to: target, form });
      if (!visited.has(target)) stack.push(target);
    }
  }
  return { files: visited, edges, unanalyzable };
}

// ── the detector's own contract (in-memory fixtures; nothing planted on disk) ─

describe("isolation detector self-check (P03(e))", () => {
  const FIXTURE = [
    // Triple-slash directives are only honoured before the first statement.
    `/// <reference path="./triple-slash.d.ts" />`,
    `import { a } from "./static-named";`,
    `import type { T } from "./type-only";`,
    `import "./bare-side-effect";`,
    `export { b } from "./re-export-named";`,
    `export * from "./re-export-star";`,
    `export type { U } from "./re-export-type";`,
    `const d = await import("./dynamic-literal");`,
    "const t = await import(`./dynamic-template-literal`);",
    `const r = require("./require-literal");`,
    "const rt = require(`./require-template-literal`);",
    `const mr = module.require("./module-require");`,
    `import eq = require("./import-equals");`,
    `type X = typeof import("./type-position-import");`,
  ].join("\n");

  it("extracts every literal import form", () => {
    const { specifiers, unanalyzable } = scanImports("fixture.ts", FIXTURE);
    const specs = specifiers.map((s) => s.spec).sort();
    expect(specs).toEqual(
      [
        "./static-named",
        "./type-only",
        "./bare-side-effect",
        "./re-export-named",
        "./re-export-star",
        "./re-export-type",
        "./dynamic-literal",
        "./dynamic-template-literal",
        "./require-literal",
        "./require-template-literal",
        "./module-require",
        "./import-equals",
        "./type-position-import",
        "./triple-slash.d.ts",
      ].sort(),
    );
    expect(unanalyzable).toEqual([]);
  });

  it.each([
    ["template with ${...}", "const m = await import(`./mod-${name}`);"],
    ["variable dynamic import", "const m = await import(path);"],
    ["concatenated dynamic import", `const m = await import("./a" + "b");`],
    ["template require", "const m = require(`./mod-${name}`);"],
    ["variable require", "const m = require(path);"],
    ["argument-less require", "const m = require();"],
    ["aliased require", "const r = require; r('./x');"],
    ["require passed as a value", "load(require);"],
    ["module.require as a value", "const r = module.require;"],
    ["createRequire", `import { createRequire } from "node:module"; const r = createRequire(x);`],
  ])("flags a non-literal specifier as unanalyzable: %s", (_label, source) => {
    expect(scanImports("fixture.ts", source).unanalyzable.length).toBeGreaterThan(0);
  });

  it("does not flag `require` in name positions", () => {
    const source = "const o = { require: 1 }; o.require; interface I { require: number }";
    expect(scanImports("fixture.ts", source).unanalyzable).toEqual([]);
  });

  it("scans forbidden literals case-insensitively in code but not in comments", () => {
    expect(forbiddenLiteralHits("f.ts", `const h = "x-api-key";`)).toEqual(["X-API-Key"]);
    expect(forbiddenLiteralHits("f.ts", `const k = "skylize_service_api_key";`)).toEqual([
      "SKYLIZE_SERVICE_API_KEY",
    ]);
    // A `//` inside a string does not start a comment that could hide code.
    expect(forbiddenLiteralHits("f.ts", `const a = "//"; const h = "X-Api-Key";`)).toEqual([
      "X-API-Key",
    ]);
    expect(forbiddenLiteralHits("f.ts", "const t = `// ${x} X-API-KEY`;")).toEqual([
      "X-API-Key",
    ]);
    expect(
      forbiddenLiteralHits("f.ts", "// X-API-Key\n/* SKYLIZE_SERVICE_API_KEY */\n/** x-api-key */\nconst a = 1;"),
    ).toEqual([]);
  });
});

// ── the isolation proof over the real tree ──────────────────────────────────

describe("My Day import isolation (design 5.0.3)", () => {
  it("discovers route entrypoints by glob, at least every route that exists today", () => {
    // Guards the whole proof: an empty or partial discovery must never pass
    // vacuously. The glob may find more routes (walked automatically), never fewer.
    expect(ENTRYPOINTS.length).toBeGreaterThanOrEqual(KNOWN_ROUTES.length);
    for (const route of KNOWN_ROUTES) {
      expect(existsSync(route), `known route missing on disk: ${route}`).toBe(true);
      expect(ENTRYPOINTS, `glob did not discover: ${route}`).toContain(route);
    }
  });

  it("reaches the expected My Day modules (walk is real, not empty)", () => {
    const { files } = reachableGraph(ENTRYPOINTS);
    const expectedReachable = [
      "myday-client.ts",
      "myday-config.ts",
      "myday-handler.ts",
      "myday-session.ts",
    ].map((n) => resolve(LIB_SKYLIZE, n));
    for (const f of expectedReachable) {
      expect(files.has(f), `expected reachable: ${f}`).toBe(true);
    }
  });

  it("has no unanalyzable (computed / non-literal) module reference anywhere in the graph", () => {
    const { unanalyzable } = reachableGraph(ENTRYPOINTS);
    expect(unanalyzable, `unanalyzable import(s):\n  ${unanalyzable.join("\n  ")}`).toEqual(
      [],
    );
  });

  it("never transitively imports client.ts, config.ts, or any Console module", () => {
    const { files, edges } = reachableGraph(ENTRYPOINTS);
    const offenders = [...files].filter((f) => FORBIDDEN_FILES.has(f));
    if (offenders.length > 0) {
      // Name the exact edge(s) that pulled a forbidden file in, for a fast fix.
      const importing = edges
        .filter((e) => FORBIDDEN_FILES.has(e.to))
        .map((e) => `${e.from} -[${e.form}]-> ${e.to}`);
      throw new Error(
        `My Day path reaches forbidden module(s): ${offenders.join(", ")}\n` +
          `via import edge(s):\n  ${importing.join("\n  ")}`,
      );
    }
    expect(offenders).toEqual([]);
  });

  it("contains no service-key literal (case-insensitive) in any reachable source file", () => {
    const { files } = reachableGraph(ENTRYPOINTS);
    const hits: string[] = [];
    for (const file of files) {
      if (!existsSync(file)) continue;
      for (const literal of forbiddenLiteralHits(file, readFileSync(file, "utf8"))) {
        hits.push(`${file}: "${literal}"`);
      }
    }
    expect(hits, `service-key literal(s) found: ${hits.join("; ")}`).toEqual([]);
  });

  it("resolves every first-party import to a file that exists (no dangling hop)", () => {
    // A dangling first-party import would be a bug that could also mask an
    // isolation regression; catch it here.
    const { edges } = reachableGraph(ENTRYPOINTS);
    const dangling = edges
      .filter((e) => e.to.startsWith(SRC_ROOT) && !existsSync(e.to))
      .map((e) => `${e.from} -> ${e.to}`);
    expect(dangling, `dangling first-party import(s): ${dangling.join("; ")}`).toEqual(
      [],
    );
  });
});
