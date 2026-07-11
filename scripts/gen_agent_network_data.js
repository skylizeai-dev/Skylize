// gen_agent_network_data.js
// Emits the canonical AgentNode[] + Department[] consumed by the website's
// <AgentNetwork/> console component, derived from the SAME sources the backend
// agent specs are generated from:
//   - docs/03_agents/_generation_manifest.csv  (authoritative: agent_name,
//     authority_level, parent_agent_id, escalation_path)
//   - scripts/agent_content.js                 (authored role + tool content)
//
// Canonicalization mirrors scripts/gen_agent_specs.js (CANON_ID, level(),
// deptOf(), the EXEC set) so the visualization never drifts from the spine.
//
// Runtime fields the manifest does NOT carry (status / tokenBudget /
// tokensUsed / tasksCompleted) are derived DETERMINISTICALLY from the agent id
// and authority level — stable across runs, no Math.random — so the rendered
// network is reproducible.
//
// Idempotent. Run from repo root:  node scripts/gen_agent_network_data.js

const fs = require("fs");
const path = require("path");

const MANIFEST = "docs/03_agents/_generation_manifest.csv";
const OUT = "website/src/components/console/agent-network.data.ts";
const CONTENT = require("./agent_content.js");

/* ── canonical id remaps (mirror gen_agent_specs.js CANON_ID) ───────── */
const CANON_ID = { vc_procurement: "vp_procurement" };
// Duplicate role files on disk that should NOT appear as separate nodes.
// `chief_product_officer` documents the same role as canonical `cpo`.
const DROP_IDS = new Set(["chief_product_officer"]);

/* ── department metadata (matches the new console design spec) ──────── */
// Palette note: 15 hues distributed around the wheel at muted saturation
// (S ≤ 45%) and a narrow luminance band, so department lanes read as
// terminal data-visualization accents on the dark canvas — never neon.
const DEPARTMENTS = [
  { id: "executive_office", name: "EXECUTIVE",       tagline: "strategy · arbitration · oversight",         color: "#4D6FE3" },
  { id: "finance",          name: "FINANCE",          tagline: "capital · FP&A · risk · treasury",           color: "#5CAD85" },
  { id: "marketing",        name: "MARKETING",        tagline: "brand · growth · SEO · email · performance", color: "#8F76C7" },
  { id: "creative",         name: "CREATIVE",         tagline: "copy · art · video · brand · ops",           color: "#BC6E86" },
  { id: "operations",       name: "OPERATIONS",       tagline: "logistics · store · supply · fulfillment",   color: "#4F9AA8" },
  { id: "procurement",      name: "PROCUREMENT",      tagline: "sourcing · contracts · vendors",             color: "#BA9A5E" },
  { id: "product",          name: "PRODUCT",          tagline: "strategy · research · experimentation",      color: "#A175B5" },
  { id: "sales",            name: "SALES",            tagline: "B2B · accounts · affiliates · partners",     color: "#85A863" },
  { id: "customer_success", name: "CUSTOMER SUCCESS", tagline: "lifecycle · retention · support",            color: "#BE7F5F" },
  { id: "engineering",      name: "ENGINEERING",      tagline: "infra · backend · frontend · devops",        color: "#5987B8" },
  { id: "data",             name: "DATA & AI",        tagline: "analytics · memory · ML · BI",               color: "#B06A9C" },
  { id: "security",         name: "SECURITY",         tagline: "zero-trust · compliance · identity",         color: "#C16A6A" },
  { id: "strategy",         name: "STRATEGY",         tagline: "competitive · M&A · expansion",              color: "#B5AC61" },
  { id: "people",           name: "PEOPLE",           tagline: "performance · training · playbooks",         color: "#7BAC84" },
  { id: "legal",            name: "LEGAL",            tagline: "privacy · contracts · compliance",           color: "#8D8AA8" },
];
const DEPT_IDS = new Set(DEPARTMENTS.map((d) => d.id));

/* ── authority level from id (mirror gen_agent_specs.js level()) ────── */
const EXEC = new Set([
  "ceo", "chief_ai_advisor", "cfo", "cmo", "coo", "cto", "cso", "cro",
  "chief_legal_officer", "chief_product_officer", "cpo", "chief_data_officer",
  "chief_security_officer", "chro",
]);
function level(n) {
  if (n.startsWith("vp_")) return "vp";
  if (n.startsWith("vc_")) return "vp";
  if (n.startsWith("director_")) return "director";
  if (n.startsWith("manager_")) return "manager";
  if (n.endsWith("_agent")) return "worker";
  if (n.endsWith("_manager")) return "manager";
  if (n.endsWith("_director")) return "director";
  if (EXEC.has(n)) return "executive";
  return "worker";
}

/* ── department from filepath (mirror gen_agent_specs.js deptOf()) ──── */
function deptOf(p) {
  const s = p.split("/");
  const i = s.indexOf("01_executive_board");
  const seg = s.slice(i + 1).map((x) => x.toLowerCase());
  if (s.includes("CSO_Security")) return "security";
  if (s.includes("CMO")) return seg.some((x) => x.includes("creative")) || s.includes("vp_creative") ? "creative" : "marketing";
  if (s.includes("CRO")) return s.includes("Sales") ? "sales" : "customer_success";
  if (s.includes("CFO")) return "finance";
  if (s.includes("CHRO")) return "people";
  if (s.includes("CLO")) return "legal";
  if (s.includes("COO")) return s.includes("Procurement") ? "procurement" : "operations";
  if (s.includes("CPO")) return "product";
  if (s.includes("CSO")) return s.includes("Special_Projects") ? "strategy" : "strategy";
  if (s.includes("CTO")) return s.includes("Data_and_AI") ? "data" : "engineering";
  return "executive_office";
}

/* ── per-level budget (mirror gen_agent_specs.js LEVEL_BUDGET[0]) ───── */
const LEVEL_BUDGET = {
  executive: 120000, vp: 80000, director: 40000, manager: 20000, worker: 10000,
};

/* ── deterministic pseudo-random in [0,1) seeded by string ──────────── */
function hash(str) {
  let h = 2166136261 >>> 0; // FNV-1a
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}
function seeded(id, salt) {
  return (hash(id + ":" + salt) % 100000) / 100000;
}

/* ── derive runtime status / usage / tasks deterministically ────────── */
const STATUSES = ["executing", "idle", "queued", "error"];
function deriveRuntime(id, lvl) {
  const r = seeded(id, "status");
  // Weight toward executing/idle; error is rare. Executives rarely "queued".
  let status;
  if (r < 0.46) status = "executing";
  else if (r < 0.84) status = "idle";
  else if (r < 0.97) status = "queued";
  else status = "error";

  const budget = LEVEL_BUDGET[lvl];
  // utilisation: executing agents run hotter; workers churn more tasks.
  const baseUtil = status === "executing" ? 0.55 + seeded(id, "util") * 0.4
                 : status === "queued" ? seeded(id, "util") * 0.18
                 : 0.15 + seeded(id, "util") * 0.4;
  const tokensUsed = Math.round(budget * Math.min(0.99, baseUtil));

  const taskScale = { executive: 220, vp: 520, director: 460, manager: 900, worker: 2600 }[lvl];
  const tasksCompleted = Math.round(taskScale * (0.35 + seeded(id, "tasks") * 1.3));

  return { status, tokenBudget: budget, tokensUsed, tasksCompleted };
}

/* ── tool parsing: agent_content tools string OR level default ──────── */
const LEVEL_TOOLS = {
  executive: "`llm.generate`, `memory.search`, `bi.query`, `orchestrator.delegate`",
  vp: "`llm.generate`, `memory.search`, `bi.query`, `orchestrator.delegate`",
  director: "`llm.generate`, `memory.search`, `orchestrator.delegate`",
  manager: "`llm.generate`, `memory.search`, `orchestrator.delegate`",
  worker: "`llm.generate`, `memory.search`",
};
const TOOL_PURPOSE = {
  "llm.generate": "language generation & reasoning",
  "memory.search": "semantic recall over governed memory",
  "bi.query": "business-intelligence queries",
  "orchestrator.delegate": "delegate work to reports",
};
function parseTools(id, lvl) {
  const raw = (CONTENT[id] && CONTENT[id].tools) || LEVEL_TOOLS[lvl];
  const names = (raw.match(/`([^`]+)`/g) || []).map((s) => s.replace(/`/g, ""));
  return names.map((name, i) => ({
    id: `${id}__t${i}`,
    name,
    purpose: TOOL_PURPOSE[name] || "supporting capability",
  }));
}

/* ── parse the manifest CSV ─────────────────────────────────────────── */
function parseCsv(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length);
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    // split on commas but the last "notes" field may itself contain commas;
    // we only need the first 5 columns, which never contain commas.
    const parts = lines[i].split(",");
    rows.push({
      filepath: parts[0],
      agent_name: parts[1],
      authority_level: parts[2],
      parent_agent_id: parts[3],
      escalation_path: parts[4],
    });
  }
  return rows;
}

function humanizeId(id) {
  return id
    .replace(/_agent$/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (m) => m.toUpperCase())
    .replace(/\bAi\b/g, "AI")
    .replace(/\bMl\b/g, "ML")
    .replace(/\bQc\b/g, "QC")
    .replace(/\bCta\b/g, "CTA")
    .replace(/\bB2b\b/g, "B2B")
    .replace(/\bSeo\b/g, "SEO")
    .replace(/\bM And A\b/g, "M&A")
    .replace(/\bFpanda\b/g, "FP&A");
}

// Display NAME — the clean human title for the card heading.
function nameFor(id) {
  const c = CONTENT[id];
  if (c && c.role) return c.role;
  return humanizeId(id);
}

// ROLE subtitle — a short descriptor that ADDS information beyond the name.
// Prefer the first responsibility bullet, else the first sentence of the
// mission, else a humanized id. Trimmed to keep cards tidy.
function roleFor(id) {
  const c = CONTENT[id] || {};
  let text = "";
  if (Array.isArray(c.resp) && c.resp[0]) text = c.resp[0];
  else if (c.mission) text = c.mission.split(/(?<=\.)\s/)[0];
  else text = humanizeId(id);
  text = text.replace(/\.$/, "").trim();
  if (text.length > 64) text = text.slice(0, 61).replace(/\s+\S*$/, "") + "…";
  return text;
}

/* ── build ──────────────────────────────────────────────────────────── */
const manifestText = fs.readFileSync(MANIFEST, "utf8");
const rows = parseCsv(manifestText);

const seen = new Set();
const agents = [];
const warnings = [];

for (const row of rows) {
  const diskId = row.agent_name;
  const id = CANON_ID[diskId] || diskId;
  if (DROP_IDS.has(id) || DROP_IDS.has(diskId)) continue;
  if (seen.has(id)) {
    warnings.push(`duplicate id collapsed: ${diskId} -> ${id}`);
    continue;
  }
  seen.add(id);

  const lvl = level(diskId);
  const dept = deptOf(row.filepath);
  if (!DEPT_IDS.has(dept)) warnings.push(`unknown dept "${dept}" for ${id}`);

  // reportsTo: parent from manifest, canonicalized. human_owner is not an agent
  // node. In the CSV every executive's parent is `human_owner` (that is the
  // GOVERNANCE escalation target). The ORG REPORTING line, per
  // 00_organization_chart.md §3-4, has the whole C-suite reporting to `ceo`,
  // with `ceo` itself the single tree root. We reflect the reporting line here
  // so the Org Map renders one centred tree under the CEO; escalationPath below
  // preserves the governance truth untouched.
  let reportsTo = row.parent_agent_id;
  reportsTo = CANON_ID[reportsTo] || reportsTo;
  if (reportsTo === "human_owner" || !reportsTo) {
    reportsTo = id === "ceo" ? null : "ceo";
  }

  // escalationPath: "a > b > human_owner" -> [a, b, human_owner], canonicalized.
  const escalationPath = row.escalation_path
    .split(">")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => CANON_ID[s] || s);

  agents.push({
    id,
    name: nameFor(id),
    role: roleFor(id),
    authority: lvl,
    department: dept,
    ...deriveRuntime(id, lvl),
    reportsTo,
    escalationPath,
    tools: parseTools(id, lvl),
  });
}

/* ── integrity checks ───────────────────────────────────────────────── */
const ids = new Set(agents.map((a) => a.id));
const roots = agents.filter((a) => a.reportsTo === null);
for (const a of agents) {
  if (a.reportsTo && !ids.has(a.reportsTo)) {
    warnings.push(`dangling reportsTo: ${a.id} -> ${a.reportsTo}`);
  }
}
// every non-root escalation should point at a known agent (except human_owner)
for (const a of agents) {
  for (const step of a.escalationPath) {
    if (step !== "human_owner" && !ids.has(step)) {
      warnings.push(`dangling escalation step in ${a.id}: ${step}`);
    }
  }
}

/* ── emit TS ────────────────────────────────────────────────────────── */
const header = `// AUTO-GENERATED by scripts/gen_agent_network_data.js — DO NOT EDIT BY HAND.
// Source of truth: docs/03_agents/_generation_manifest.csv + scripts/agent_content.js
// Regenerate:  node scripts/gen_agent_network_data.js
//
// ${agents.length} agents · ${DEPARTMENTS.length} departments · ${roots.length} root(s)

import type { AgentNode, Department } from "./agent-network.types";
`;

const deptLines = DEPARTMENTS.map(
  (d) => `  { id: ${JSON.stringify(d.id)}, name: ${JSON.stringify(d.name)}, tagline: ${JSON.stringify(d.tagline)}, color: ${JSON.stringify(d.color)} },`,
).join("\n");

function agentLine(a) {
  const tools = a.tools
    .map((t) => `{ id: ${JSON.stringify(t.id)}, name: ${JSON.stringify(t.name)}, purpose: ${JSON.stringify(t.purpose)} }`)
    .join(", ");
  return (
    `  { id: ${JSON.stringify(a.id)}, name: ${JSON.stringify(a.name)}, role: ${JSON.stringify(a.role)}, ` +
    `authority: ${JSON.stringify(a.authority)}, department: ${JSON.stringify(a.department)}, ` +
    `status: ${JSON.stringify(a.status)}, tokenBudget: ${a.tokenBudget}, tokensUsed: ${a.tokensUsed}, ` +
    `tasksCompleted: ${a.tasksCompleted}, reportsTo: ${a.reportsTo === null ? "null" : JSON.stringify(a.reportsTo)}, ` +
    `escalationPath: [${a.escalationPath.map((s) => JSON.stringify(s)).join(", ")}], ` +
    `tools: [${tools}] },`
  );
}

const body = `${header}
export const DEPARTMENTS: Department[] = [
${deptLines}
];

export const AGENTS: AgentNode[] = [
${agents.map(agentLine).join("\n")}
];
`;

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, body, "utf8");

console.log(`Wrote ${OUT}`);
console.log(`  agents: ${agents.length}`);
console.log(`  departments: ${DEPARTMENTS.length}`);
console.log(`  roots (reportsTo=null): ${roots.length} -> ${roots.map((r) => r.id).join(", ")}`);
const byLevel = {};
for (const a of agents) byLevel[a.authority] = (byLevel[a.authority] || 0) + 1;
console.log(`  by level:`, byLevel);
const byDept = {};
for (const a of agents) byDept[a.department] = (byDept[a.department] || 0) + 1;
console.log(`  by dept:`, byDept);
if (warnings.length) {
  console.log(`\n  WARNINGS (${warnings.length}):`);
  for (const w of warnings) console.log(`   - ${w}`);
} else {
  console.log(`\n  no integrity warnings.`);
}
