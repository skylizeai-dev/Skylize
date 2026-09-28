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
// HOW THE WALK WORKS
// ------------------
// Starting from the two real route entrypoints (app/api/my-day/*/route.ts), we
// read each file from disk, extract its import specifiers, resolve every
// RELATIVE import and every `@/lib/skylize/*` import to a concrete .ts file, and
// recurse — building the transitive first-party import graph exactly as the
// bundler would. `next/server`, `zod`, and other bare package specifiers are
// leaves (not first-party, not part of the service-key concern). The test then
// asserts:
//   1. No file in that graph is one of the forbidden Console modules
//      (client.ts, handler.ts, proxy-gate.ts, session.ts) or config.ts.
//   2. No file in that graph contains the literal `X-API-Key` or reads
//      `SKYLIZE_SERVICE_API_KEY`.
//
// A future edit that adds `import { getBackendConfig } from "./config"` (or any
// hop that transitively pulls client.ts in) FAILS this test, because config.ts
// / client.ts would then appear in the reachable set.

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// This test file lives at website/src/lib/skylize/. Anchor everything from the
// `website/src` root so the walk is independent of the process cwd.
const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(HERE, "..", ".."); // website/src
const LIB_SKYLIZE = resolve(SRC_ROOT, "lib", "skylize");

// The actual entrypoints Next.js invokes for the My Day namespace.
const ENTRYPOINTS = [
  resolve(SRC_ROOT, "app", "api", "my-day", "session", "route.ts"),
  resolve(SRC_ROOT, "app", "api", "my-day", "me", "route.ts"),
];

// Forbidden modules — importing (even transitively) any of these violates the
// isolation guarantee. Resolved to absolute paths for exact comparison.
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
// reachable My Day source file. Checked against CODE only (comments stripped):
// the My Day modules deliberately NAME these in their doc comments to document
// the prohibition ("It NEVER reads SKYLIZE_SERVICE_API_KEY", "client.ts
// attaches X-API-Key ... this module does not"), and that prose is the design
// intent, not a violation — only a real code reference is a regression.
const FORBIDDEN_LITERALS = ["X-API-Key", "SKYLIZE_SERVICE_API_KEY"];

/**
 * Strip `//` line comments and block comments from TS source so the literal
 * scan sees only executable code. Not a full tokenizer (a `//` inside a string
 * literal would be over-stripped), but it is conservative in the safe
 * direction: it can only REMOVE text, so a genuine code reference to a
 * forbidden literal — which never sits inside a comment — is always still seen.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ") // block comments
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1"); // line comments (not scheme "://")
}

/** Extract every import/export-from specifier string from TS source. */
function importSpecifiers(source: string): string[] {
  const specs: string[] = [];
  // `import ... from "x"`, `export ... from "x"`, and bare `import "x"`.
  const re =
    /(?:import|export)\s+(?:[^'"]*?\s+from\s+)?["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    const spec = m[1] ?? m[2];
    if (spec) specs.push(spec);
  }
  return specs;
}

/**
 * Resolve a first-party import specifier (relative, or the `@/` alias) to a
 * concrete .ts file on disk. Returns null for bare package specifiers
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
  // Try the exact path, then .ts, then .tsx, then /index.ts.
  const candidates = [base, `${base}.ts`, `${base}.tsx`, resolve(base, "index.ts")];
  for (const c of candidates) {
    if (existsSync(c) && c.endsWith(".ts")) return c;
    if (existsSync(c) && c.endsWith(".tsx")) return c;
  }
  // A first-party spec that does not resolve is itself suspicious; surface it as
  // the resolved-but-missing path so a broken import can't silently pass.
  return `${base}.ts`;
}

/** Transitive first-party import graph reachable from `roots`. */
function reachableGraph(roots: string[]): {
  files: Set<string>;
  edges: Array<{ from: string; to: string }>;
} {
  const visited = new Set<string>();
  const edges: Array<{ from: string; to: string }> = [];
  const stack = [...roots];

  while (stack.length > 0) {
    const file = stack.pop() as string;
    if (visited.has(file)) continue;
    visited.add(file);

    if (!existsSync(file)) {
      // A root or resolved import that does not exist is a real failure to
      // surface, not a leaf to skip.
      continue;
    }
    const source = readFileSync(file, "utf8");
    for (const spec of importSpecifiers(source)) {
      const target = resolveFirstParty(spec, file);
      if (target === null) continue; // bare package leaf
      edges.push({ from: file, to: target });
      if (!visited.has(target)) stack.push(target);
    }
  }
  return { files: visited, edges };
}

describe("My Day import isolation (design 5.0.3)", () => {
  it("has both route entrypoints present on disk", () => {
    // Guards the whole proof: if the entrypoints moved, an empty/half graph
    // could pass vacuously. This fails loudly if the surface under test is gone.
    for (const ep of ENTRYPOINTS) {
      expect(existsSync(ep), `entrypoint missing: ${ep}`).toBe(true);
    }
  });

  it("reaches the expected My Day modules (walk is real, not empty)", () => {
    const { files } = reachableGraph(ENTRYPOINTS);
    // The walk must actually traverse the My Day lib modules, or the isolation
    // assertions below would be proving nothing.
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

  it("never transitively imports client.ts, config.ts, or any Console module", () => {
    const { files, edges } = reachableGraph(ENTRYPOINTS);
    const offenders = [...files].filter((f) => FORBIDDEN_FILES.has(f));
    if (offenders.length > 0) {
      // Name the exact edge(s) that pulled a forbidden file in, for a fast fix.
      const importing = edges
        .filter((e) => FORBIDDEN_FILES.has(e.to))
        .map((e) => `${e.from} -> ${e.to}`);
      throw new Error(
        `My Day path reaches forbidden module(s): ${offenders.join(", ")}\n` +
          `via import edge(s):\n  ${importing.join("\n  ")}`,
      );
    }
    expect(offenders).toEqual([]);
  });

  it("contains no service-key literal in any reachable source file", () => {
    const { files } = reachableGraph(ENTRYPOINTS);
    const hits: string[] = [];
    for (const file of files) {
      if (!existsSync(file)) continue;
      const code = stripComments(readFileSync(file, "utf8"));
      for (const literal of FORBIDDEN_LITERALS) {
        if (code.includes(literal)) hits.push(`${file}: "${literal}"`);
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
