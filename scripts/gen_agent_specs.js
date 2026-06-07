// gen_agent_specs.js
// Renders the canonical 14-section agent role specification into every agent .md
// under docs/03_agents/01_executive_board, using authored per-agent content (DATA)
// and the manifest-derived authority_level / parent / escalation_path.
//
// Consistency rules (must match the spine):
//   authority levels: executive/vp/director/manager/worker  (agent_governance.md §2)
//   failure modes: retry_then_escalate/escalate_immediately/fail_closed/fallback_degraded
//   token validation order, event taxonomy, memory namespaces — per spine docs.
//
// Idempotent: rewrites each agent file from DATA + manifest. Run from repo root.

const fs = require("fs"), path = require("path");
const ROOT = "docs/03_agents/01_executive_board";
const SKIP = new Set([]); // none — every .md under ROOT is an agent

// ---------- manifest logic (mirrors scripts/gen_manifest.js) ----------
function walk(d, acc) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name).split(path.sep).join("/");
    if (e.isDirectory()) walk(p, acc);
    else if (e.name.endsWith(".md")) acc.push(p);
  }
  return acc;
}
const aid = (p) => path.basename(p).replace(/\.md$/, "");
const EXEC = new Set([
  "ceo","chief_ai_advisor","cfo","cmo","coo","cto","cso","cro",
  "chief_legal_officer","chief_product_officer","cpo","chief_data_officer",
  "chief_security_officer","chro",
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
const order = { worker: 0, manager: 1, director: 2, vp: 3, executive: 4 };
let files = walk(ROOT, []).sort();
const byDir = {};
for (const p of files) { const d = path.dirname(p); (byDir[d] = byDir[d] || []).push(p); }
function chain(p) {
  const cur = order[level(aid(p))] ?? 0;
  const found = {};
  let d = path.dirname(p);
  while (d && d !== "docs/03_agents") {
    for (const c of (byDir[d] || [])) {
      if (c === p) continue;
      const r = order[level(aid(c))] ?? -1;
      if (r > cur && !(r in found)) found[r] = aid(c);
    }
    d = path.dirname(d);
  }
  const out = [];
  for (let r = cur + 1; r <= 4; r++) if (r in found) out.push(found[r]);
  out.push("human_owner");
  return out;
}
function deptOf(p) {
  // owning department channel inferred from path segment under 01_executive_board
  const s = p.split("/");
  const i = s.indexOf("01_executive_board");
  const seg = s.slice(i + 1).map(x => x.toLowerCase());
  if (seg.some(x => x.includes("cso_security")) || seg.some(x => x === "managers" && s.join("/").includes("CSO_Security"))) return "security";
  if (s.includes("CSO_Security")) return "security";
  if (s.includes("CMO")) return seg.some(x => x.includes("creative")) || s.includes("vp_creative") ? "creative" : "marketing";
  if (s.includes("CRO")) return s.includes("Sales") ? "sales" : "customer_success";
  if (s.includes("CFO")) return "finance";
  if (s.includes("CHRO")) return "people";
  if (s.includes("CLO")) return "legal";
  if (s.includes("COO")) return s.includes("Procurement") ? "procurement" : "operations";
  if (s.includes("CPO")) return "product";
  if (s.includes("CSO")) return s.includes("Special_Projects") ? "special_projects" : "strategy";
  if (s.includes("CTO")) return s.includes("Data_and_AI") ? "data" : "engineering";
  return "executive_office";
}

// canonical agent_id override for manifest-flagged typos (path preserved on disk)
const CANON_ID = { vc_procurement: "vp_procurement" };
const KNOWN_ISSUE = {
  vc_procurement: "Path uses `vc_procurement` (typo); canonical `agent_id` is `vp_procurement`.",
  cpo: "Duplicate CPO role on disk (`CPO/Product/cpo.md` and `CPO/chief_product_officer.md`); canonical executive is `cpo`.",
  chief_product_officer: "Duplicate CPO role file; canonical executive `agent_id` is `cpo`. This file documents the same role.",
};

// ---------- defaults by level ----------
const LEVEL_FAILURE = {
  executive: "escalate_immediately",
  vp: "retry_then_escalate",
  director: "retry_then_escalate",
  manager: "retry_then_escalate",
  worker: "fallback_degraded",
};
const LEVEL_BUDGET = {
  executive: [120000, 600], vp: [80000, 420], director: [40000, 300],
  manager: [20000, 180], worker: [10000, 90],
};
const LEVEL_HITL = {
  executive: ["SPEND_OVER_CEILING","BRAND_LEGAL_SENSITIVE","LOW_CONFIDENCE_IRREVERSIBLE"],
  vp: ["FIRST_EXTERNAL_LAUNCH","BRAND_LEGAL_SENSITIVE","SPEND_OVER_CEILING"],
  director: ["BRAND_LEGAL_SENSITIVE","SPEND_OVER_CEILING"],
  manager: ["BRAND_LEGAL_SENSITIVE"],
  worker: [],
};
const LEVEL_AUTH = {
  executive: "Company/function-wide strategy, top-level budget envelopes, cross-team arbitration. Must escalate owner-reserved actions (legal commitments, irreversible over-ceiling spend).",
  vp: "Function strategy and approvals; reallocate budget within the function cap; approve external launches within risk policy. Must escalate cross-function trade-offs and over-cap spend.",
  director: "Owns a department workflow; approves its outputs; allocates within a delegated cap; launches internal-only actions. Must escalate over-cap spend and brand/legal-sensitive launches.",
  manager: "Routes/QAs worker outputs; approves within a small pre-set threshold. Must escalate any spend, external publish, or cross-team coordination.",
  worker: "Produces its single bounded artifact; reads granted memory; calls allowed tools within budget. Escalates anything beyond its task; never authorizes spend or external launches.",
};
const LEVEL_TOOLS = {
  executive: '`llm.generate`, `memory.search`, `bi.query`, `orchestrator.delegate`',
  vp: '`llm.generate`, `memory.search`, `bi.query`, `orchestrator.delegate`',
  director: '`llm.generate`, `memory.search`, `orchestrator.delegate`',
  manager: '`llm.generate`, `memory.search`, `orchestrator.delegate`',
  worker: '`llm.generate`, `memory.search`',
};

module.exports = { /* exported for tests if needed */ };

// ---------- DATA: authored per-agent content ----------
// Keyed by canonical agent_id. Each entry supplies the role-specific substance;
// structure + governance defaults come from the level. Fields:
//   role, mission, resp[], kpis, inputs, outputs, deps, consumes, produces,
//   readMem[], writeMem[], failureNote, (optional) tools, hitl, failure
const DATA = require("./agent_content.js");

// ---------- render ----------
function bullets(arr) { return arr.map(x => "- " + x).join("\n"); }
function render(p) {
  const diskId = aid(p);
  const id = CANON_ID[diskId] || diskId;
  const lvl = level(diskId);
  const dept = deptOf(p);
  const esc = chain(p).join(" > ");
  const d = DATA[id] || DATA[diskId] || {};
  const role = d.role || id.replace(/_/g, " ");
  const [budget, secs] = LEVEL_BUDGET[lvl];
  const tools = d.tools || LEVEL_TOOLS[lvl];
  const hitl = (d.hitl || LEVEL_HITL[lvl]);
  const failure = d.failure || LEVEL_FAILURE[lvl];
  const readMem = d.readMem || (lvl === "executive" ? ["org:*","strategy:*"] : [`${dept}:*`]);
  const writeMem = d.writeMem || (lvl === "worker" ? [] : [`${dept}:approved`]);
  const issue = KNOWN_ISSUE[diskId];

  const lines = [];
  lines.push(`# Agent: \`${id}\``);
  lines.push("");
  lines.push(`**Authority level:** \`${lvl}\` · **Department:** \`${dept}\` · **Escalation path:** \`${esc}\``);
  lines.push(`**Related:** [00_organization_chart.md](${rel(p,"docs/03_agents/00_organization_chart.md")}) · [agent_governance.md](${rel(p,"docs/03_agents/agent_governance.md")}) · [agent_contract_registry.md](${rel(p,"docs/03_agents/agent_contract_registry.md")})`);
  if (issue) { lines.push(""); lines.push(`> **Known issue (from manifest):** ${issue} Path preserved on disk; this spec uses the canonical \`agent_id\`.`); }
  lines.push(""); lines.push("---"); lines.push("");
  lines.push(`## 1. Mission`); lines.push(d.mission || `Own the ${role} outcome within its authority, under governance.`); lines.push("");
  lines.push(`## 2. Responsibilities`); lines.push(bullets(d.resp || [`Execute the ${role} mandate.`, "Operate within contract scope and governance.", "Escalate beyond-authority decisions."])); lines.push("");
  lines.push(`## 3. Authority Scope`); lines.push(`\`${lvl}\`. ${LEVEL_AUTH[lvl]}`); lines.push("");
  lines.push(`## 4. Escalation Rules`); lines.push(`Escalation path: \`${esc}\`. On a beyond-authority decision or a \`${failure}\` failure, the Decision Engine routes the proposal to the next entry, emitting \`governance.human_escalation_raised\` ([agent_governance.md §3](${rel(p,"docs/03_agents/agent_governance.md")}#3-authority--escalation)).`); lines.push("");
  lines.push(`## 5. KPIs`); lines.push(d.kpis || "Throughput, quality, and zero ungoverned actions within its mandate."); lines.push("");
  lines.push(`## 6. Inputs`); lines.push(`\`${d.inputs || `skylize.schemas.${dept}.${pascal(id)}In`}\` — ${d.inputsNote || "the scoped work item it consumes (validated against its contract `input_schema`)."}`); lines.push("");
  lines.push(`## 7. Outputs`); lines.push(`\`${d.outputs || `skylize.schemas.${dept}.${pascal(id)}Out`}\` — ${d.outputsNote || "its produced artifact, wrapped by the Orchestrator into the correct event."}`); lines.push("");
  lines.push(`## 8. Dependencies`); lines.push(d.deps || "The Orchestrator, Governance Authority, Decision Engine, Memory service, and its parent/children in the org tree."); lines.push("");
  lines.push(`## 9. Events Consumed`); lines.push(bullets(d.consumes || ["`decision.approved` (work authorized to proceed)", "relevant departmental events on its channel"])); lines.push("");
  lines.push(`## 10. Events Produced`); lines.push(bullets(d.produces || ["its typed output, wrapped as a department event by the Orchestrator", "`audit.action_recorded` for every action"])); lines.push("");
  lines.push(`## 11. OPA Governance Requirements`); lines.push(`\`allowed_tools\`: ${tools}. Token \`scope\` ⊆ \`allowed_tools\`, validated signature → expiry → revocation → scope → budget → delegation. \`governance_token_required = true\`. \`max_token_budget = ${budget}\`, \`max_execution_time_seconds = ${secs}\`. \`human_in_loop_triggers\`: ${hitl.length ? hitl.map(h=>"`"+h+"`").join(", ") : "none (bounded task)"}.`); lines.push("");
  lines.push(`## 12. Memory Requirements`); lines.push(`**Read:** ${readMem.map(m=>"`"+m+"`").join(", ")}. **Write:** ${writeMem.length ? writeMem.map(m=>"`"+m+"`").join(", ") : "none — proposes via `memory.write_requested`; the Memory service persists (workers do not write stores directly)."}`); lines.push("");
  lines.push(`## 13. Success Metrics`); lines.push(d.success || "Outputs accepted by its parent/Decision Engine; SLOs met; no scope or budget violations; full audit trail."); lines.push("");
  lines.push(`## 14. Failure Conditions`); lines.push(`\`failure_mode = ${failure}\`. ${d.failureNote || "Failure = invalid/over-scope output, budget/time overrun, or denial; repeated violations trip the circuit breaker ([agent_governance.md §7](" + rel(p,"docs/03_agents/agent_governance.md") + "#7-circuit-breaker-rules)). Kill-switch/suspension state overrides all authority."}`); lines.push("");
  return lines.join("\n");
}
function pascal(s){return s.split("_").map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join("");}
function rel(from, to){
  let r = path.relative(path.dirname(from), to).split(path.sep).join("/");
  if (!r.startsWith(".")) r = "./" + r;
  return r;
}

// ---------- main ----------
let written = 0;
for (const p of files) {
  fs.writeFileSync(p, render(p), "utf8");
  written++;
}
console.log("Agent specs written:", written);
