const fs = require("fs"), path = require("path");
const ROOT = "docs/03_agents";
const SKIP = new Set([
  "agent_governance.md", "agent_contract_registry.md", "00_organization_chart.md",
  "system_boundaries.md", "event_driven_architecture.md",
  "_generation_manifest.csv", "_BUILD_LOG.md",
]);

function walk(d, acc) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name).split(path.sep).join("/");
    if (e.isDirectory()) walk(p, acc);
    else if (e.name.endsWith(".md") && !SKIP.has(e.name)) acc.push([p, fs.statSync(p).size]);
  }
  return acc;
}
let files = walk(ROOT, []).sort((a, b) => (a[0] < b[0] ? -1 : 1));
const aid = (p) => path.basename(p).replace(/\.md$/, "");

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
  return "UNKNOWN";
}
const order = { worker: 0, manager: 1, director: 2, vp: 3, executive: 4 };
const byDir = {};
for (const [p] of files) { const d = path.dirname(p); (byDir[d] = byDir[d] || []).push(p); }

function findParent(p) {
  const cur = order[level(aid(p))] ?? 0;
  let d = path.dirname(p);
  while (d && d !== "docs/03_agents") {
    let best = null, br = -1;
    for (const c of (byDir[d] || [])) {
      if (c === p) continue;
      const r = order[level(aid(c))] ?? 0;
      if (r > cur && r > br) { best = c; br = r; }
    }
    if (best) return aid(best);
    d = path.dirname(d);
  }
  return "human_owner";
}
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
function stray(p, size) {
  if (size === 0) return false;
  const t = fs.readFileSync(p, "utf8").trim();
  return /^[\w./-]+\.md$/.test(t);
}
const nameCount = {};
for (const [p] of files) { const n = aid(p); (nameCount[n] = nameCount[n] || []).push(p); }

const rows = [];
for (const [p, size] of files) {
  const name = aid(p), lvl = level(name), parent = findParent(p), ch = chain(p).join(" > ");
  const segs = p.split("/"), notes = [];
  if (nameCount[name].length > 1) notes.push("DUPLICATE name x" + nameCount[name].length);
  if (name === "vc_procurement") notes.push("RENAME->vp_procurement (vc typo)");
  if (name === "cpo" || name === "chief_product_officer") notes.push("DUP CPO role");
  if (name === "director_mna" || name === "director_m_and_a") notes.push("M&A naming variant");
  if (lvl === "UNKNOWN") notes.push("AUTHORITY UNINFERRABLE");
  if (p.includes("/CSO_Security/managers/workers/")) notes.push("PATH: worker under managers/workers/ (expected workers/)");
  if (p.includes("/COO/Procurement/managers/workers/")) notes.push("PATH: worker under Procurement/managers/workers/");
  if (lvl === "director" && segs.includes("managers")) notes.push("DEPTH CONTRADICTION: director inside managers/ dir");
  if (lvl === "worker" && !segs.includes("workers")) notes.push("DEPTH: worker not under workers/ dir");
  if (segs.some((s) => s.includes("departmant"))) notes.push("TYPO in path: 'departmant'");
  if (stray(p, size)) notes.push("STRAY-TEXT FILE (" + size + "b) - clean before generation");
  else if (size > 0) notes.push("non-empty (" + size + "b)");
  rows.push([p, name, lvl, parent, ch, notes.join("; ")]);
}
function csvq(s) { return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
const out = "docs/03_agents/_generation_manifest.csv";
fs.writeFileSync(out, ["filepath,agent_name,authority_level,parent_agent_id,escalation_path,notes",
  ...rows.map((r) => r.map(csvq).join(","))].join("\n") + "\n", "utf8");

const cnt = {};
for (const r of rows) cnt[r[2]] = (cnt[r[2]] || 0) + 1;
console.log("TOTAL:", rows.length, "BY LEVEL:", JSON.stringify(cnt));
console.log("\nDUPLICATE NAMES:");
for (const n of Object.keys(nameCount).sort()) if (nameCount[n].length > 1) console.log("  " + n + ":\n    " + nameCount[n].join("\n    "));
console.log("\nSTRAY-TEXT FILES:");
for (const [p, s] of files) if (stray(p, s)) console.log("  " + s + "b  " + p + "  ->  " + JSON.stringify(fs.readFileSync(p, "utf8").trim()));
console.log("\nUNKNOWN AUTHORITY:");
for (const r of rows) if (r[2] === "UNKNOWN") console.log("  " + r[0]);
console.log("\nDEPTH/PATH/TYPO ANOMALIES:");
for (const r of rows) if (/DEPTH|PATH|TYPO/.test(r[5])) console.log("  " + r[1] + " :: " + r[5]);
console.log("\nManifest:", out);
