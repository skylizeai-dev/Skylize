import { useEffect, useRef, useState, useCallback } from 'react';
import { DEPARTMENTS, AGENTS } from '../data/agentNetworkData.js';

const byId = {};
AGENTS.forEach((a) => { byId[a.id] = a; });
const deptById = {};
DEPARTMENTS.forEach((d) => { deptById[d.id] = d; });

const CONNECTORS = [
  { name: 'Slack', mono: 'SL', color: '#7A9BFF' },
  { name: 'Salesforce', mono: 'SF', color: '#34C579' },
  { name: 'Stripe', mono: 'ST', color: '#A78BFA' },
  { name: 'Snowflake', mono: 'SN', color: '#7ACBE0' },
  { name: 'GitHub', mono: 'GH', color: '#BC6E86' },
  { name: 'Zendesk', mono: 'ZD', color: '#5CAD85' },
  { name: 'Google Drive', mono: 'GD', color: '#D19A3F' },
];

const WFS = [
  { name: 'Campaign Launch', meta: 'MARKETING · 6 STAGES', desc: 'Brief to live campaign with brand and budget gates before spend.', active: 2, stages: [['Brief intake', 'Director, Growth'], ['Creative sprint', 'Creative Director'], ['Brand gate', 'Brand Guardian'], ['Budget gate', 'VP Finance'], ['Human approval', 'Operator'], ['Launch & monitor', 'Dir, Performance Mktg']] },
  { name: 'Vendor Onboarding', meta: 'PROCUREMENT · 5 STAGES', desc: 'Discovery through contract with risk scoring at every step.', active: 3, stages: [['Discovery', 'Vendor Discovery'], ['Evaluation', 'Vendor Evaluation'], ['Risk score', 'Vendor Risk'], ['Contract review', 'Director, Contracts'], ['Human approval', 'Operator']] },
  { name: 'Monthly Close', meta: 'FINANCE · 5 STAGES', desc: 'Ledger reconciliation to CFO summary, fully signed.', active: 1, stages: [['Reconcile', 'Director, Treasury'], ['Variance scan', 'Director, Risk'], ['Forecast update', 'Director, FP&A'], ['CFO review', 'CFO'], ['Owner sign-off', 'Operator']] },
  { name: 'Incident Response', meta: 'ENGINEERING · 4 STAGES', desc: 'Detect, contain, and post-mortem with security co-sign.', active: 0, stages: [['Detect & triage', 'Director, DevOps'], ['Contain', 'Dir, Agent Infrastructure'], ['Root cause', 'Director, Backend'], ['Post-mortem', 'CTO']] },
  { name: 'Content Pipeline', meta: 'CREATIVE · 5 STAGES', desc: 'Hooks to published assets with QC and style gates.', active: 4, stages: [['Hooks', 'Hook Generator'], ['Draft', 'Script Writer'], ['Style gate', 'Style Guardian'], ['Visual QC', 'Visual QC'], ['Publish', 'Creative Ops Manager']] },
];

function utcNow() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
}
function fk(n) {
  return n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'K' : String(n);
}
function money(n) {
  return '$' + Math.round(n).toLocaleString('en-US');
}
function statusColor(s) {
  return s === 'executing' ? '#34C579' : s === 'error' ? '#E15A52' : s === 'queued' ? '#D19A3F' : '#6B7383';
}
function authShort(a) {
  return a === 'executive' ? 'EXEC' : a === 'vp' ? 'VP' : a === 'director' ? 'DIR' : a === 'manager' ? 'MGR' : 'WKR';
}
function fmtSize(b) {
  return b < 1024 ? b + ' B' : b < 1e6 ? (b / 1024).toFixed(0) + ' KB' : (b / 1e6).toFixed(1) + ' MB';
}
function routeDept(text) {
  const t = text.toLowerCase();
  if (/(campaign|brand|launch|social|ad|creative|content)/.test(t)) return 'marketing';
  if (/(revenue|forecast|budget|spend|margin|cost|profit)/.test(t)) return 'finance';
  if (/(churn|retention|customer|support|nps)/.test(t)) return 'customer_success';
  if (/(security|audit|compliance|breach|access)/.test(t)) return 'security';
  if (/(vendor|supplier|procure|contract|sourcing)/.test(t)) return 'procurement';
  if (/(deploy|ship|latency|bug|infra|api)/.test(t)) return 'engineering';
  return 'strategy';
}
function seedStr(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function mulberry(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const seedPulse = [
  { time: '21:04:12', agent: 'Director, SEO', action: 'memory.search · 214 hits' },
  { time: '21:04:07', agent: 'VP Finance', action: 'orchestrator.delegate → Director, FP&A' },
  { time: '21:03:58', agent: 'Brand Guardian', action: 'policy gate PASS · asset #4417' },
  { time: '21:03:51', agent: 'Hook Generator', action: 'draft.v3 submitted for review' },
  { time: '21:03:44', agent: 'Director, Risk', action: 'anomaly scan · 0 flags' },
  { time: '21:03:31', agent: 'Vendor Discovery', action: 'bi.query OK · 412ms' },
  { time: '21:03:19', agent: 'CMO', action: 'budget request → approval queue' },
  { time: '21:03:02', agent: 'Retention Optimizer', action: 'drop-off model refreshed' },
  { time: '21:02:47', agent: 'Director, DevOps', action: 'contract gate PASS · build 8841' },
  { time: '21:02:33', agent: 'Pricing Negotiation', action: 'target price computed · SKU-2291' },
  { time: '21:02:21', agent: 'Style Guardian', action: 'style check PASS · 6 assets' },
  { time: '21:02:06', agent: 'CFO', action: 'ceiling adjusted · marketing +4%' },
];
const seedAudit = [
  { time: '21:03:40', actor: 'Chief Financial Officer', action: 'DIRECTIVE', target: 'finance/q3-reforecast', sig: '0x8F41…C2A9' },
  { time: '21:01:12', actor: 'Operator (human)', action: 'APPROVAL', target: 'approvals/AP-2214 · APPROVED', sig: '0x77D0…19EE' },
  { time: '20:58:03', actor: 'Director, Compliance', action: 'SECURITY', target: 'policy/pii-redaction · verified', sig: '0xA3B8…77F1' },
  { time: '20:55:47', actor: 'VP Marketing', action: 'TOOL', target: 'ads.push · scope check PASS', sig: '0x5C19…B44D' },
  { time: '20:51:22', actor: 'Manager, Budgeting', action: 'BILLING', target: 'budget/creative · +$2,400', sig: '0xE801…6A3C' },
  { time: '20:48:15', actor: 'Chief Legal Officer', action: 'DIRECTIVE', target: 'legal/contract-review-batch', sig: '0x19AD…F0B2' },
  { time: '20:44:58', actor: 'Director, Privacy', action: 'SECURITY', target: 'consent-gate/learning-pipeline', sig: '0xB6E2…3D17' },
  { time: '20:41:31', actor: 'VP Engineering', action: 'TOOL', target: 'deploy.staging · contract gate', sig: '0x40C7…88A5' },
  { time: '20:37:09', actor: 'Operator (human)', action: 'APPROVAL', target: 'approvals/AP-2209 · DECLINED', sig: '0x92F3…1CB0' },
  { time: '20:33:44', actor: 'Director, Sourcing', action: 'BILLING', target: 'po/vendor-8812 · $18,750', sig: '0x6D5A…E4F8' },
  { time: '20:29:12', actor: 'CTO', action: 'DIRECTIVE', target: 'engineering/latency-audit', sig: '0x03BB…7A61' },
  { time: '20:24:56', actor: 'Approval Manager', action: 'TOOL', target: 'approvals.sync · 14 items', sig: '0xC4E9…20D3' },
];

function loadPersisted() {
  try { return JSON.parse(localStorage.getItem('skylize.console.v2') || '{}'); } catch (e) { return {}; }
}

function initialState() {
  const persisted = loadPersisted();
  return {
    screen: (persisted.screen === 'ag' || persisted.screen === 'net') ? 'star' : (persisted.screen || 'dash'),
    sidebarOpen: persisted.sidebarOpen !== false,
    notifOpen: false,
    clock: utcNow(),
    tokRate: 182400,
    dirInput: '',
    messages: [], chatInput: '', chatBusy: false, chatPhase: '', rail: [], sessTok: 0,
    chatAttachments: [], chatTags: [], chatConnectors: [], tagMenuOpen: false, connMenuOpen: false, tagQ: '',
    agQ: '', agDept: 'all', agAuth: 'all', selId: null,
    netSel: persisted.netSel || null,
    starDeptIdx: (function (list) { const i = list.findIndex((d) => d.id === persisted.netSel); return i < 0 ? 0 : i; })(DEPARTMENTS),
    starQ: '',
    wfSel: 0,
    apRisk: 'all',
    approvals: [
      { id: 'AP-2216', title: 'Increase Q3 paid-social budget by $12,400', meta: 'Director, Performance Marketing · 14 min ago', chain: 'Dir → VP Marketing → CMO → YOU', risk: 'HIGH', amount: '$12,400' },
      { id: 'AP-2217', title: 'Send master services agreement to the enterprise client', meta: 'Director, Contracts · 26 min ago', chain: 'Dir → CLO → YOU', risk: 'HIGH', amount: '$86,000' },
      { id: 'AP-2218', title: 'Launch reactivation email to 48,200 dormant accounts', meta: 'Director, Email Marketing · 41 min ago', chain: 'Dir → VP Marketing → YOU', risk: 'MED', amount: '—' },
      { id: 'AP-2219', title: 'Switch worker tier routing to Nova-2 for 72h load test', meta: 'Chief AI Advisor · 1 h ago', chain: 'Advisor → CTO → YOU', risk: 'MED', amount: '−$310/day' },
      { id: 'AP-2220', title: 'Approve vendor PO — packaging refresh pilot', meta: 'Director, Sourcing · 2 h ago', chain: 'Dir → VP Procurement → COO → YOU', risk: 'LOW', amount: '$4,150' },
      { id: 'AP-2221', title: 'Export anonymized churn cohort to BI workspace', meta: 'Director, Retention · 3 h ago', chain: 'Dir → Privacy gate → YOU', risk: 'LOW', amount: '—' },
    ],
    logFilter: 'all', audit: seedAudit,
    pulse: seedPulse,
    orgName: persisted.orgName || 'Aventra Retail Group', region: persisted.region || 'eu-central',
    retention: persisted.retention || '365', autonomy: persisted.autonomy != null ? persisted.autonomy : 2,
    guards: persisted.guards || { cap: true, email: true, pii: true, fallback: false },
    pausedAll: false, pauseArm: false, toast: null,
  };
}

export function useConsoleState(props) {
  const accent = (props && props.accent) || '#3D6BFF';
  const motion = (props && props.motion) || 'immersive';
  const [state, setStateRaw] = useState(initialState);
  const stateRef = useRef(state);
  stateRef.current = state;

  const setState = useCallback((patch) => {
    setStateRaw((prev) => {
      const next = typeof patch === 'function' ? patch(prev) : patch;
      return { ...prev, ...next };
    });
  }, []);

  const persist = useCallback(() => {
    const s = stateRef.current;
    try {
      localStorage.setItem('skylize.console.v2', JSON.stringify({
        screen: s.screen, sidebarOpen: s.sidebarOpen, netSel: s.netSel, orgName: s.orgName,
        region: s.region, retention: s.retention, autonomy: s.autonomy, guards: s.guards,
      }));
    } catch (e) {}
  }, []);

  const set = useCallback((patch) => { setState(patch); queueMicrotask(persist); }, [setState, persist]);

  const timers = useRef([]);
  const t = useCallback((fn, ms) => { const id = setTimeout(fn, ms); timers.current.push(id); return id; }, []);

  const sparksRef = useRef(null);
  if (!sparksRef.current) {
    let seed = 1337;
    const rand = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    const sparks = [];
    for (let k = 0; k < 6; k++) {
      let v = 8 + rand() * 8; const pts = [];
      for (let i = 0; i < 12; i++) { v = Math.max(2, Math.min(20, v + (rand() - 0.48) * 5)); pts.push((i * 5.8 + 1).toFixed(1) + ',' + (22 - v).toFixed(1)); }
      sparks.push(pts.join(' '));
    }
    const spend30 = []; let sv = 1900;
    for (let i = 0; i < 30; i++) { sv = Math.max(1200, Math.min(3400, sv + (rand() - 0.44) * 420)); spend30.push(Math.round(sv)); }
    sparksRef.current = { sparks, spend30 };
  }
  const { sparks, spend30 } = sparksRef.current;

  const chatElRef = useRef(null);
  const fileInputElRef = useRef(null);
  const starTiltElRef = useRef(null);
  const starPlaneElRef = useRef(null);
  const sgCache = useRef({});
  const geomCache = useRef(null);
  const toastTRef = useRef(null);
  const armTRef = useRef(null);
  const reduceMotionRef = useRef(false);

  const showToast = useCallback((text, dot) => {
    clearTimeout(toastTRef.current);
    setState({ toast: { text, dot: dot || '#34C579' } });
    toastTRef.current = setTimeout(() => setState({ toast: null }), 3200);
  }, [setState]);

  // ── nav / misc ──
  const nav = useCallback((id) => {
    set({ screen: id, notifOpen: false, selId: null });
    if (id === 'star') t(() => flyStars(1), 55);
  }, [set, t]);

  const tiltMove = useCallback((e) => {
    if (reduceMotionRef.current) return;
    const el = e.currentTarget, r = el.getBoundingClientRect();
    const max = motion === 'calm' ? 2 : 5;
    const rx = ((e.clientY - r.top) / r.height - 0.5) * -2 * max;
    const ry = ((e.clientX - r.left) / r.width - 0.5) * 2 * max;
    el.style.transform = 'perspective(900px) rotateX(' + rx.toFixed(2) + 'deg) rotateY(' + ry.toFixed(2) + 'deg) translateZ(6px)';
  }, [motion]);
  const tiltLeave = useCallback((e) => { e.currentTarget.style.transform = ''; }, []);

  // ── chat simulation ──
  const onAttachClick = useCallback(() => { if (fileInputElRef.current) fileInputElRef.current.click(); }, []);
  const onFileChange = useCallback((e) => {
    const files = Array.from(e.target.files || []).map((f) => ({ name: f.name, size: fmtSize(f.size) }));
    if (files.length) setState((s) => ({ chatAttachments: s.chatAttachments.concat(files) }));
    e.target.value = '';
  }, [setState]);
  const removeAttachment = useCallback((i) => setState((s) => { const a = s.chatAttachments.slice(); a.splice(i, 1); return { chatAttachments: a }; }), [setState]);
  const toggleTagMenu = useCallback(() => setState((s) => ({ tagMenuOpen: !s.tagMenuOpen, connMenuOpen: false, tagQ: '' })), [setState]);
  const toggleConnMenu = useCallback(() => setState((s) => ({ connMenuOpen: !s.connMenuOpen, tagMenuOpen: false })), [setState]);
  const onTagQ = useCallback((e) => setState({ tagQ: e.target.value }), [setState]);
  const addTag = useCallback((tag) => setState((s) => {
    if (s.chatTags.some((x) => x.id === tag.id)) return { tagMenuOpen: false };
    return { chatTags: s.chatTags.concat([tag]), tagMenuOpen: false, tagQ: '' };
  }), [setState]);
  const removeTag = useCallback((i) => setState((s) => { const a = s.chatTags.slice(); a.splice(i, 1); return { chatTags: a }; }), [setState]);
  const addConnector = useCallback((c) => setState((s) => {
    if (s.chatConnectors.some((x) => x.name === c.name)) return { connMenuOpen: false };
    return { chatConnectors: s.chatConnectors.concat([c]), connMenuOpen: false };
  }), [setState]);
  const removeConnector = useCallback((i) => setState((s) => { const a = s.chatConnectors.slice(); a.splice(i, 1); return { chatConnectors: a }; }), [setState]);

  const buildReply = useCallback((dept, chain, tok) => {
    const R = {
      marketing: ['Directive accepted — routed to Marketing & Creative.', 'Campaign brief drafted; Copy and Art directors assigned worker pools.', 'Channel budget allocation proposed within CMO ceiling — no approval required.', 'Brand Guardian gate scheduled before any asset ships.', 'First deliverables land in Deliverables within the hour; launch order will surface in Approvals.'],
      finance: ['Directive accepted — routed to Finance.', 'FP&A is re-running the forecast against the live pipeline.', 'Director, Risk is scanning variance and spend anomalies in parallel.', 'Treasury reconciliation pinned to the new baseline.', 'CFO summary with confidence bands will be posted to Deliverables shortly.'],
      customer_success: ['Directive accepted — routed to Customer Success.', 'Retention is scoring churn-risk cohorts against the last 90 days.', 'Lifecycle playbooks queued for the top three at-risk segments.', 'Escalations above autonomy tier will surface in your Approvals queue.', 'Expect the cohort report in Deliverables within the hour.'],
      security: ['Directive accepted — routed to Security & Legal.', 'Access review sweep started across all 151 agent tool grants.', 'Compliance is re-verifying SOC 2 control evidence.', 'Privacy gate re-checked on the learning pipeline — PASS.', 'Signed report will be attached to the Audit Log on completion.'],
      procurement: ['Directive accepted — routed to Procurement.', 'Vendor Discovery is building a shortlist against your criteria.', 'Pricing Negotiation computing target prices from historical POs.', 'Contract Review will flag risk before anything is committed.', 'Committed spend will pause for your approval — nothing is signed autonomously.'],
      engineering: ['Directive accepted — routed to Engineering.', 'Pipeline created; DevOps contract gate armed for the change.', 'Canary rollout plan drafted with automatic rollback thresholds.', 'Latency budget checks wired to the observability stream.', 'Deploy order above autonomy tier will surface in Approvals.'],
      strategy: ['Directive accepted — decomposed by the Orchestrator.', 'Strategy directors are framing options with competitive context.', 'Finance validates the numbers before anything reaches you.', 'Synthesis memo with a recommendation lands in Deliverables.', 'You will only be interrupted if a decision exceeds the autonomy envelope.'],
    };
    const lines = (R[dept.id] || R.strategy).map((txt, i) => (i === 0 ? { k: 'b', t: txt } : (i < 4 ? { k: 'i', t: txt, m: '0' + i } : { k: 'p', t: txt })));
    const hex = Math.floor(Math.random() * 65535).toString(16).toUpperCase().padStart(4, '0');
    return { who: 'ai', lines, gov: { token: 'GRN-' + hex, agents: chain.length, tokens: fk(tok) } };
  }, []);

  const doSend = useCallback((text) => {
    text = (text || '').trim();
    const s = stateRef.current;
    const atts = s.chatAttachments, tags = s.chatTags, conns = s.chatConnectors;
    if (!text && !atts.length) return;
    if (s.chatBusy) return;
    const deptTag = tags.find((tg) => tg.deptId && !tg.agentId);
    const agentTag = tags.find((tg) => tg.agentId);
    let forcedAgent = null, deptId = null;
    if (agentTag) { forcedAgent = byId[agentTag.agentId]; deptId = forcedAgent ? forcedAgent.department : null; }
    if (!deptId && deptTag) deptId = deptTag.deptId;
    if (!deptId) deptId = routeDept(text || 'strategy update');
    const dept = deptById[deptId] || DEPARTMENTS[0] || { name: 'STRATEGY', color: '#B5AC61' };
    const chips = []
      .concat(atts.length ? [{ label: atts.length + (atts.length === 1 ? ' FILE' : ' FILES'), color: '#77809A', dotDisp: 'none' }] : [])
      .concat(tags.map((tg) => ({ label: '#' + tg.label.toUpperCase(), color: tg.color, dotDisp: 'block' })))
      .concat(conns.map((c) => ({ label: 'VIA ' + c.name.toUpperCase(), color: c.color, dotDisp: 'block' })));
    const user = { who: 'user', lines: text ? [{ k: 'p', t: text }] : [{ k: 'p', t: 'Shared ' + atts.length + ' file' + (atts.length === 1 ? '' : 's') + ' for review.' }], chips };
    setState((prev) => ({ ...prev, messages: prev.messages.concat([user]), chatInput: '', dirInput: '', chatBusy: true, chatPhase: 'ROUTING VIA ORCHESTRATOR', rail: [], chatAttachments: [], chatTags: [], chatConnectors: [] }));
    const pool = AGENTS.filter((a) => a.department === deptId);
    let chain;
    if (forcedAgent) {
      chain = [forcedAgent].concat(AGENTS.filter((a) => a.reportsTo === forcedAgent.id).slice(0, 4));
    } else {
      chain = [
        pool.find((a) => a.authority === 'executive'),
        pool.find((a) => a.authority === 'vp'),
        pool.filter((a) => a.authority === 'director')[0],
        pool.filter((a) => a.authority === 'director')[1],
        pool.filter((a) => a.authority === 'manager' || a.authority === 'worker')[0],
        pool.filter((a) => a.authority === 'worker')[1],
      ].filter(Boolean).slice(0, 6);
    }
    t(() => setState({ chatPhase: 'DELEGATING · ' + dept.name }), 900);
    chain.forEach((a, i) => {
      t(() => setState((prev) => ({ ...prev, rail: prev.rail.concat([{ id: a.id, name: a.name, depth: Math.min(i, 3), dept: a.department, done: false, tok: 400 + Math.floor(Math.random() * 2400) }]) })), 1400 + i * 420);
    });
    const execAt = 1700 + chain.length * 420;
    t(() => setState({ chatPhase: 'EXECUTING · ' + chain.length + ' AGENTS' }), execAt);
    t(() => {
      setState((prev) => {
        const tok = prev.rail.reduce((sum, r) => sum + r.tok, 0) + 900;
        const rail = prev.rail.map((r) => ({ ...r, done: true }));
        const reply = buildReply(dept, chain, tok);
        return { ...prev, chatBusy: false, chatPhase: '', rail, messages: prev.messages.concat([reply]), sessTok: prev.sessTok + tok };
      });
    }, execAt + 2200);
  }, [setState, t, buildReply]);

  // ── starmap ──
  const starGeom = useCallback((id) => {
    if (!AGENTS.length) return { nodes: [], lines: [], head: null };
    if (sgCache.current[id]) return sgCache.current[id];
    const cx = 380, cy = 250;
    const RANK = { executive: 0, vp: 1, director: 2, manager: 3, worker: 4 };
    const rankOf = (a) => (RANK[a.authority] != null ? RANK[a.authority] : 4);
    const A = AGENTS.filter((a) => a.department === id).slice().sort((a, b) => rankOf(a) - rankOf(b) || b.tokensUsed - a.tokensUsed);
    const nodes = [], pos = {}, lines = [];
    if (!A.length) { const r0 = { nodes, lines, head: null }; sgCache.current[id] = r0; return r0; }
    const rng = mulberry(seedStr(id));
    const R = { 0: 66, 1: 96, 2: 150, 3: 200, 4: 232 };
    const SZ = { 0: 22, 1: 18, 2: 15, 3: 12, 4: 9 };
    const ZZ = { 0: 38, 1: 28, 2: 20, 3: 12, 4: 6 };
    const head = A[0];
    const hsz = (SZ[rankOf(head)] || 16) + 3;
    nodes.push({ id: head.id, size: hsz, left: cx - hsz / 2, top: cy - hsz / 2, z: 40, exec: head.status === 'executing', rank: rankOf(head) });
    pos[head.id] = { x: cx, y: cy };
    const groups = {};
    A.slice(1).forEach((a) => { const r = rankOf(a); (groups[r] = groups[r] || []).push(a); });
    Object.keys(groups).forEach((rk) => {
      const list = groups[rk], k = list.length, base = R[rk] || 210, worker = rk === '4';
      const step = (Math.PI * 2) / k, off = rng() * Math.PI * 2;
      list.forEach((a, i) => {
        let radius = base;
        if (worker && k > 10) radius = (i % 2 === 0) ? base - 22 : base + 8;
        radius += (rng() - 0.5) * 12;
        const ang = off + i * step + (rng() - 0.5) * step * 0.3;
        const x = cx + Math.cos(ang) * radius, y = cy + Math.sin(ang) * radius * 0.9;
        const sz = SZ[rk] || 9;
        nodes.push({ id: a.id, size: sz, left: x - sz / 2, top: y - sz / 2, z: ZZ[rk] || 6, exec: a.status === 'executing', rank: Number(rk) });
        pos[a.id] = { x, y };
      });
    });
    const inDept = {}; A.forEach((a) => { inDept[a.id] = true; });
    A.forEach((a) => {
      if (a === head) return;
      const tgt = (a.reportsTo && inDept[a.reportsTo]) ? a.reportsTo : head.id;
      const p = pos[a.id], qp = pos[tgt];
      if (p && qp) lines.push({ x1: p.x, y1: p.y, x2: qp.x, y2: qp.y, strong: tgt === head.id || rankOf(a) === 2 });
    });
    const res = { nodes, lines, head: head.id };
    sgCache.current[id] = res; return res;
  }, []);

  function flyStars(dir) {
    const plane = starPlaneElRef.current;
    if (!plane || !plane.animate || reduceMotionRef.current) return;
    try {
      plane.animate([{ opacity: 0, transform: 'translateX(' + (dir * 64) + 'px) scale(0.96)' }, { opacity: 1, transform: 'none' }], { duration: 520, easing: 'cubic-bezier(.2,.8,.2,1)' });
      plane.querySelectorAll('.sk-star').forEach((el, i) => el.animate([{ opacity: 0, transform: 'scale(0.2)' }, { opacity: 1, transform: 'scale(1)' }], { duration: 460, delay: Math.min(i * 13, 430), easing: 'cubic-bezier(.34,1.56,.64,1)', fill: 'backwards' }));
      plane.querySelectorAll('.sk-line').forEach((el, i) => el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 620, delay: 130 + i * 7, fill: 'backwards' }));
    } catch (e) {}
  }

  const starStep = useCallback((dir) => {
    const n = DEPARTMENTS.length || 1;
    const idx = ((stateRef.current.starDeptIdx + dir) % n + n) % n;
    const d = DEPARTMENTS[idx];
    setState({ starDeptIdx: idx, selId: null, starQ: '', netSel: d ? d.id : null });
    persist(); flyStars(dir);
  }, [setState, persist]);
  const starGo = useCallback((idx) => {
    const dir = idx >= stateRef.current.starDeptIdx ? 1 : -1, d = DEPARTMENTS[idx];
    setState({ starDeptIdx: idx, selId: null, starQ: '', netSel: d ? d.id : null });
    persist(); flyStars(dir);
  }, [setState, persist]);

  const starTilt = useCallback((e) => {
    const el = starTiltElRef.current;
    if (!el || reduceMotionRef.current) return;
    const r = e.currentTarget.getBoundingClientRect();
    const mx = (e.clientX - r.left) / r.width - 0.5, my = (e.clientY - r.top) / r.height - 0.5;
    const amp = motion === 'calm' ? 4 : 9;
    el.style.transform = 'rotateX(' + (-my * amp).toFixed(2) + 'deg) rotateY(' + (mx * amp).toFixed(2) + 'deg)';
  }, [motion]);
  const starTiltReset = useCallback(() => { if (starTiltElRef.current) starTiltElRef.current.style.transform = 'rotateX(0deg) rotateY(0deg)'; }, []);

  const netGeom = useCallback(() => {
    if (geomCache.current) return geomCache.current;
    const execs = AGENTS.filter((a) => a.authority === 'executive' && a.id !== 'ceo');
    const vps = AGENTS.filter((a) => a.authority === 'vp');
    const dots = [], lines = [];
    execs.forEach((a, i) => {
      const ang = (i / execs.length) * Math.PI * 2 - Math.PI / 2, r = 95;
      const d = deptById[a.department] || { color: '#7A86A6' };
      dots.push({ dept: a.department, x: 280 + Math.cos(ang) * r - 9, y: 280 + Math.sin(ang) * r - 9, size: 18, color: d.color, exec: a.status === 'executing' });
    });
    vps.forEach((a, i) => {
      const ang = (i / vps.length) * Math.PI * 2 - Math.PI / 3, r = 165;
      const d = deptById[a.department] || { color: '#7A86A6' };
      dots.push({ dept: a.department, x: 280 + Math.cos(ang) * r - 6, y: 280 + Math.sin(ang) * r - 6, size: 12, color: d.color, exec: a.status === 'executing' });
    });
    DEPARTMENTS.forEach((d, i) => {
      const ang = (i / DEPARTMENTS.length) * Math.PI * 2 - Math.PI / 2, r = 245;
      const members = AGENTS.filter((a) => a.department === d.id);
      const size = Math.min(26, 10 + members.length * 0.55);
      dots.push({ dept: d.id, x: 280 + Math.cos(ang) * r - size / 2, y: 280 + Math.sin(ang) * r - size / 2, size, color: d.color, exec: members.some((a) => a.status === 'executing') });
      lines.push({ w: '245px', tf: 'rotate(' + (ang * 180 / Math.PI).toFixed(1) + 'deg)', c: d.color + '55' });
    });
    geomCache.current = { dots, lines };
    return geomCache.current;
  }, []);

  // ── lifecycle ──
  useEffect(() => {
    reduceMotionRef.current = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const iv1 = setInterval(() => {
      setState((s) => ({ clock: utcNow(), tokRate: Math.max(120000, s.tokRate + Math.round((Math.random() - 0.48) * 6000)) }));
    }, 5000);
    const iv2 = setInterval(() => {
      const agents = ['Director, SEO', 'Hook Generator', 'VP Finance', 'Brand Guardian', 'Vendor Risk', 'Pacing', 'Director, Treasury', 'Caption Writer', 'Supplier Score', 'Director, Growth', 'Visual QC', 'Manager, Profitability'];
      const acts = ['memory.search · ' + (40 + Math.floor(Math.random() * 400)) + ' hits', 'orchestrator.delegate → worker pool', 'policy gate PASS', 'bi.query OK · ' + (120 + Math.floor(Math.random() * 600)) + 'ms', 'draft submitted for review', 'budget check within ceiling', 'tool grant verified', 'task complete · signed'];
      const d = new Date(); const p = (n) => String(n).padStart(2, '0');
      const entry = { time: p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds()), agent: agents[Math.floor(Math.random() * agents.length)], action: acts[Math.floor(Math.random() * acts.length)] };
      setState((s) => ({ pulse: [entry].concat(s.pulse).slice(0, 40) }));
    }, 3400);
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); nav('chat'); return; }
      if (stateRef.current.screen !== 'star') return;
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.key === 'ArrowRight') { e.preventDefault(); starStep(1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); starStep(-1); }
    };
    window.addEventListener('keydown', onKey);
    if (stateRef.current.screen === 'star') t(() => flyStars(1), 60);
    return () => {
      clearInterval(iv1); clearInterval(iv2);
      window.removeEventListener('keydown', onKey);
      clearTimeout(toastTRef.current); clearTimeout(armTRef.current);
      timers.current.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (chatElRef.current) chatElRef.current.scrollTop = chatElRef.current.scrollHeight;
  }, [state.messages, state.chatBusy]);

  const decide = useCallback((id, ok) => {
    const s = stateRef.current;
    const d = new Date(); const p = (n) => String(n).padStart(2, '0');
    setState((prev) => ({
      ...prev,
      approvals: prev.approvals.filter((a) => a.id !== id),
      audit: [{ time: p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds()), actor: 'Operator (human)', action: 'APPROVAL', target: 'approvals/' + id + ' · ' + (ok ? 'APPROVED' : 'DECLINED'), sig: '0x' + Math.floor(Math.random() * 65535).toString(16).toUpperCase().padStart(4, '0') + '…' + Math.floor(Math.random() * 65535).toString(16).toUpperCase().padStart(4, '0') }].concat(prev.audit),
    }));
    showToast(id + ' ' + (ok ? 'APPROVED' : 'DECLINED') + ' · SIGNED TO AUDIT LOG', ok ? '#34C579' : '#E15A52');
  }, [setState, showToast]);

  const dangerPause = useCallback(() => {
    const s = stateRef.current;
    if (s.pausedAll) { setState({ pausedAll: false, pauseArm: false }); showToast('DELEGATION RESUMED', '#34C579'); return; }
    if (!s.pauseArm) { clearTimeout(armTRef.current); setState({ pauseArm: true }); armTRef.current = setTimeout(() => setState({ pauseArm: false }), 3000); return; }
    clearTimeout(armTRef.current); setState({ pausedAll: true, pauseArm: false }); showToast('ALL AGENTS PAUSED · APPROVALS QUEUE STAYS LIVE', '#E15A52');
  }, [setState, showToast]);

  return buildViewModel({
    state, accent, chatElRef, fileInputElRef, starTiltElRef, starPlaneElRef,
    setState, set, nav, tiltMove, tiltLeave,
    onAttachClick, onFileChange, removeAttachment, toggleTagMenu, toggleConnMenu, onTagQ, addTag, removeTag, addConnector, removeConnector,
    doSend, starGeom, starStep, starGo, starTilt, starTiltReset, netGeom, decide, dangerPause, showToast, sparks, spend30,
  });
}

function buildViewModel(ctx) {
  const {
    state: s, accent: acc, chatElRef, fileInputElRef, starTiltElRef, starPlaneElRef,
    setState, set, nav, tiltMove, tiltLeave,
    onAttachClick, onFileChange, removeAttachment, toggleTagMenu, toggleConnMenu, onTagQ, addTag, removeTag, addConnector, removeConnector,
    doSend, starGeom, starStep, starGo, starTilt, starTiltReset, netGeom, decide, dangerPause, showToast, sparks, spend30,
  } = ctx;

  const active = AGENTS.filter((a) => a.status === 'executing').length;
  const totalTok = AGENTS.reduce((tt, a) => tt + a.tokensUsed, 0);
  const crumbs = { dash: 'overview', chat: 'command', star: 'starmap', wf: 'workflows', appr: 'approvals', ana: 'analytics', mod: 'models', kb: 'knowledge', integ: 'integrations', dev: 'developers', log: 'audit log', sec: 'security', team: 'team', bill: 'billing', set: 'settings' };
  const open = s.sidebarOpen;

  const ico = {
    dash: 'M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z',
    chat: 'M2.5 3h11v7.5H7.5L4.5 13v-2.5h-2z',
    ag: 'M3.5 5.5h9v7h-9zM8 5.5V3M5.8 9h.01M10.2 9h.01',
    star: 'M8 1.9l1.7 3.9 4.2.4-3.2 2.8 1 4.1L8 10.9 4.3 13.1l1-4.1L2.1 6.2l4.2-.4z',
    net: 'M8 2.5v3.5M8 6l-4.5 6.5M8 6l4.5 6.5M3.5 12.5h9',
    wf: 'M2.5 3h4.5v4.5H2.5zM9 8.5h4.5V13H9zM7 5.2h4.8M4.7 7.5v3.2H9',
    appr: 'M8 2l5 2v4.2c0 2.8-2 4.4-5 5.8-3-1.4-5-3-5-5.8V4zM5.8 8l1.6 1.6 2.8-3.1',
    ana: 'M3 13V8.5M6.3 13V4M9.7 13V6.5M13 13V3',
    mod: 'M5 5h6v6H5zM8 2v3M8 11v3M2 8h3M11 8h3',
    kb: 'M3 4.2c0-1 2.2-1.7 5-1.7s5 .7 5 1.7v7.6c0 1-2.2 1.7-5 1.7s-5-.7-5-1.7zM3 4.2c0 1 2.2 1.7 5 1.7s5-.7 5-1.7M3 8c0 1 2.2 1.7 5 1.7S13 9 13 8',
    integ: 'M5.2 2.5v3M10.8 2.5v3M4 5.5h8v2.8a4 4 0 01-8 0zM8 12.3V14',
    dev: 'M5.5 5L2.5 8l3 3M10.5 5l3 3-3 3',
    log: 'M3 4.5L6.5 8 3 11.5M8.5 12.5H13',
    sec: 'M4.5 7.5h7V13h-7zM5.8 7.5V5.6a2.2 2.2 0 014.4 0v1.9',
    team: 'M5.5 7.2a2.1 2.1 0 100-4.2 2.1 2.1 0 000 4.2zM2.5 13c0-2.1 1.3-3.6 3-3.6s3 1.5 3 3.6M10.6 6.8a1.9 1.9 0 10-.4-3.7M9.8 9.6c1.7.2 3 1.7 3 3.4',
    bill: 'M2.5 3.5h11v9h-11zM2.5 6.5h11M5 9.5h2.5',
    set: 'M2.5 4.5h11M2.5 8h11M2.5 11.5h11M6 3.2v2.6M10.5 6.7v2.6M4.8 10.2v2.6',
  };
  const groups = [
    { label: 'OPERATE', items: [['dash', 'Dashboard'], ['chat', 'Command'], ['star', 'Starmap'], ['wf', 'Workflows'], ['appr', 'Approvals']] },
    { label: 'INTELLIGENCE', items: [['ana', 'Analytics'], ['mod', 'Models'], ['kb', 'Knowledge']] },
    { label: 'PLATFORM', items: [['integ', 'Integrations'], ['dev', 'Developers'], ['log', 'Audit Log']] },
    { label: 'GOVERNANCE', items: [['sec', 'Security'], ['team', 'Team'], ['bill', 'Billing'], ['set', 'Settings']] },
  ];
  const navGroups = groups.map((g) => ({
    label: g.label,
    items: g.items.map((pair) => {
      const id = pair[0], on = s.screen === id;
      return {
        click: () => nav(id), label: pair[1], icon: ico[id],
        bg: on ? 'color-mix(in oklab, var(--accent,#3D6BFF) 14%, transparent)' : 'transparent',
        c: on ? '#E9EBF2' : '#8B93A7',
        edgeBg: on ? 'var(--accent,#3D6BFF)' : 'transparent',
        badgeDisp: id === 'appr' && s.approvals.length && open ? 'inline-block' : 'none',
        badge: String(s.approvals.length),
      };
    }),
  }));

  // ── dashboard ──
  const tiers = [['worker', 'WORKERS'], ['manager', 'MANAGERS'], ['director', 'DIRECTORS'], ['vp', 'VPS'], ['executive', 'C-SUITE']];
  const tierLayers = tiers.map((tt, i) => {
    const n = AGENTS.filter((a) => a.authority === tt[0]).length;
    const top = i === 4;
    return {
      go: () => { nav('star'); },
      label: tt[1], count: String(n),
      w: (262 - i * 24) + 'px', h: (170 - i * 16) + 'px',
      tf: 'translate(-50%,-50%) translateZ(' + (i * 19) + 'px)',
      bd: top ? 'color-mix(in oklab, var(--accent,#3D6BFF) 65%, transparent)' : 'rgba(122,134,166,' + (0.18 + i * 0.07).toFixed(2) + ')',
      bg: top ? 'color-mix(in oklab, var(--accent,#3D6BFF) 16%, rgba(12,15,22,0.85))' : 'rgba(14,17,25,' + (0.55 + i * 0.08).toFixed(2) + ')',
      c: top ? 'color-mix(in oklab, var(--accent,#3D6BFF) 70%, #FFFFFF)' : '#8B93A7',
    };
  });
  const cost = totalTok * 0.0009;
  const errN = AGENTS.filter((a) => a.status === 'error').length;
  const kpis = [
    { label: 'DIRECTIVES · 24H', value: '34', sub: 'issued', delta: '+6 vs yday', deltaColor: '#34C579', spark: sparks[0], sparkColor: acc, valColor: '#E9EBF2' },
    { label: 'TOKENS / HR', value: fk(s.tokRate), sub: 'live', delta: '+4.1%', deltaColor: '#34C579', spark: sparks[1], sparkColor: acc, valColor: '#E9EBF2' },
    { label: 'SPEND · TODAY', value: money(cost), sub: 'USD', delta: '−2.8% vs avg', deltaColor: '#34C579', spark: sparks[2], sparkColor: '#34C579', valColor: '#E9EBF2' },
    { label: 'PENDING APPROVALS', value: String(s.approvals.length), sub: 'in queue', delta: 'oldest 3h', deltaColor: '#D19A3F', spark: sparks[3], sparkColor: '#D19A3F', valColor: '#D19A3F' },
    { label: 'AGENTS IN ERROR', value: String(errN), sub: 'of 151', delta: 'auto-restart armed', deltaColor: '#E15A52', spark: sparks[4], sparkColor: '#E15A52', valColor: errN ? '#E15A52' : '#E9EBF2' },
  ];
  const deptTok = DEPARTMENTS.map((d) => ({ d, tok: AGENTS.filter((a) => a.department === d.id).reduce((tt, a) => tt + a.tokensUsed, 0) })).sort((a, b) => b.tok - a.tok);
  const maxTok = deptTok.length ? deptTok[0].tok : 1;
  const deptBars = deptTok.slice(0, 8).map((x) => ({ name: x.d.name, color: x.d.color, w: Math.round(x.tok / maxTok * 100) + '%', amt: money(x.tok * 0.0009) }));
  const riskC = { HIGH: '#E15A52', MED: '#D19A3F', LOW: '#34C579' };
  const apPrev = s.approvals.slice(0, 3).map((a) => ({ title: a.title, meta: a.meta, risk: a.risk, riskColor: riskC[a.risk], riskBg: riskC[a.risk] + '14', approve: () => decide(a.id, true), decline: () => decide(a.id, false) }));
  const agentColors = ['#7A9BFF', '#34C579', '#D19A3F', '#A78BFA', '#5CAD85', '#BC6E86'];
  const pulseLogs = s.pulse.map((l) => ({ time: l.time, agent: l.agent, agentColor: agentColors[l.agent.length % agentColors.length], action: l.action }));

  // ── chat ──
  const sugg = ['Reforecast Q3 revenue with the current pipeline', 'Launch a win-back campaign for dormant accounts', 'Audit all vendor contracts up for renewal'];
  const messagesVm = s.messages.map((m) => {
    const user = m.who === 'user';
    return {
      align: user ? 'flex-end' : 'flex-start',
      bg: user ? 'color-mix(in oklab, var(--accent,#3D6BFF) 26%, #0C0F16)' : '#0E1119',
      border: user ? 'color-mix(in oklab, var(--accent,#3D6BFF) 45%, #1B2130)' : '#1B2130',
      color: user ? '#F2F4FA' : '#D6D9E2',
      lines: m.lines.map((ln) => ({ isBold: ln.k === 'b', isItem: ln.k === 'i', isPlain: ln.k === 'p', text: ln.t, marker: ln.m || '' })),
      govShow: !!m.gov, govToken: m.gov ? m.gov.token : '', govAgents: m.gov ? String(m.gov.agents) : '', govTokens: m.gov ? m.gov.tokens : '',
      chipsShow: !!(m.chips && m.chips.length),
      chips: (m.chips || []).map((c) => ({ label: c.label, color: c.color, dotDisp: c.dotDisp })),
    };
  });
  const tagQq = s.tagQ.toLowerCase();
  const deptTagDefs = DEPARTMENTS.map((d) => ({ id: 'dept:' + d.id, kind: 'DEPT', label: d.name, color: d.color, deptId: d.id }));
  const peopleTagDefs = AGENTS.filter((a) => a.authority === 'executive' || a.authority === 'vp').map((a) => ({ id: 'agent:' + a.id, kind: a.authority === 'executive' ? 'EXEC' : 'VP', label: a.name, color: (deptById[a.department] || { color: '#7A86A6' }).color, agentId: a.id, deptId: a.department }));
  const tagOptions = deptTagDefs.concat(peopleTagDefs).filter((tg) => !tagQq || tg.label.toLowerCase().includes(tagQq)).slice(0, 40).map((tg) => ({ pick: () => addTag(tg), label: tg.label, color: tg.color, kind: tg.kind }));
  const connOptions = CONNECTORS.map((c) => ({ pick: () => addConnector(c), name: c.name, mono: c.mono, color: c.color }));
  const composerChips = []
    .concat(s.chatAttachments.map((f, i) => ({ label: f.name + ' · ' + f.size, c: '#C7CBD6', dot: '#77809A', dotDisp: 'none', fileDisp: 'inline-flex', remove: () => removeAttachment(i) })))
    .concat(s.chatTags.map((tg, i) => ({ label: '#' + tg.label, c: tg.color, dot: tg.color, dotDisp: 'none', fileDisp: 'none', remove: () => removeTag(i) })))
    .concat(s.chatConnectors.map((c, i) => ({ label: 'VIA ' + c.name.toUpperCase(), c: '#9AA1B2', dot: c.color, dotDisp: 'block', fileDisp: 'none', remove: () => removeConnector(i) })));
  const railAgents = s.rail.map((r) => {
    const d = deptById[r.dept] || { color: '#7A86A6' };
    return { pad: (12 + r.depth * 12) + 'px', dot: r.done ? '#34C579' : 'var(--accent,#3D6BFF)', anim: r.done ? 'none' : 'pulseDot 1.3s ease-in-out infinite', name: r.name, deptColor: d.color, statusWord: r.done ? 'DONE · SIGNED' : 'EXECUTING', tok: fk(r.tok) };
  });

  // ── agents (backing data for starmap detail panel) ──
  const barC = (p) => (p > 88 ? '#E15A52' : p > 65 ? '#D19A3F' : '#34C579');
  const selA = s.selId ? byId[s.selId] : null;
  let sel = { name: '', id: '', role: '', auth: '', statusColor: '', statusLabel: '', deptColor: '', deptName: '', tasksFmt: '', tokensFmt: '', budgetFmt: '', barColor: '', barW: '0%', pct: '0', tools: [], path: [] };
  if (selA) {
    const d = deptById[selA.department] || { name: '—', color: '#7A86A6' };
    const pct = Math.round(selA.tokensUsed / selA.tokenBudget * 100);
    sel = {
      name: selA.name, id: selA.id.toUpperCase(), role: selA.role, auth: selA.authority,
      statusColor: statusColor(selA.status), statusLabel: selA.status.toUpperCase(), deptColor: d.color, deptName: d.name,
      tasksFmt: fk(selA.tasksCompleted), tokensFmt: fk(selA.tokensUsed), budgetFmt: fk(selA.tokenBudget),
      barColor: barC(pct), barW: Math.min(100, pct) + '%', pct: String(pct),
      tools: selA.tools.map((tt) => ({ name: tt.name, purpose: tt.purpose })),
      path: selA.escalationPath.map((p, i) => ({ arrow: '→'.repeat(i + 1), name: byId[p] ? byId[p].name : (p === 'human_owner' ? 'Human owner (you)' : p) })),
    };
  }

  // ── starmap ──
  const nDept = DEPARTMENTS.length || 1;
  const starIdx = ((s.starDeptIdx % nDept) + nDept) % nDept;
  const sdept = DEPARTMENTS[starIdx] || { name: '—', tagline: '', color: '#7A86A6', id: '' };
  const sgeom = starGeom(sdept.id);
  const smembers = AGENTS.filter((a) => a.department === sdept.id).slice().sort((a, b) => b.tokensUsed - a.tokensUsed);
  const sq = s.starQ.toLowerCase();
  const smatch = (a) => !sq || a.name.toLowerCase().includes(sq) || (a.role && a.role.toLowerCase().includes(sq));
  const shead = byId[sgeom.head];
  const starNodes = sgeom.nodes.map((n) => {
    const a = byId[n.id], on = s.selId === n.id, dim = sq && a && !smatch(a);
    return {
      click: () => setState({ selId: n.id }),
      left: n.left.toFixed(1) + 'px', top: n.top.toFixed(1) + 'px', size: n.size + 'px', z: n.z + 'px',
      color: sdept.color, glow: (n.exec ? 17 : 7) + 'px',
      op: dim ? '0.12' : (s.selId && !on ? '0.5' : '1'),
      ring: on ? ', 0 0 0 2px #FFFFFF, 0 0 24px ' + sdept.color : '',
      anim: n.exec ? 'twinkle 2.8s ease-in-out infinite' : 'none',
      labelShow: ((n.rank <= 2) || on) && !dim ? 'block' : 'none',
      label: a ? a.name : '', scale: on ? '1.4' : '1',
    };
  });
  const starLines = sgeom.lines.map((l) => ({ x1: l.x1.toFixed(1), y1: l.y1.toFixed(1), x2: l.x2.toFixed(1), y2: l.y2.toFixed(1), stroke: sdept.color, w: l.strong ? '1.1' : '0.6', op: l.strong ? '0.5' : '0.2' }));
  const starDots = DEPARTMENTS.map((d, i) => ({ click: () => starGo(i), bg: i === starIdx ? d.color : '#2A3244', w: i === starIdx ? '22px' : '7px', glow: i === starIdx ? '0 0 8px ' + d.color : 'none' }));
  const starList = smembers.map((a) => {
    const on = s.selId === a.id, dim = sq && !smatch(a);
    return { click: () => setState({ selId: a.id }), name: a.name, tok: fk(a.tokensUsed), auth: authShort(a.authority), statusColor: statusColor(a.status), c: on ? '#E9EBF2' : (dim ? '#616A82' : '#C7CBD6'), bg: on ? 'rgba(255,255,255,0.05)' : 'transparent' };
  });
  const starExecN = smembers.filter((a) => a.status === 'executing').length;

  // ── workflows ──
  const wf = WFS[s.wfSel];
  const wfList = WFS.map((w, i) => ({ pick: () => setState({ wfSel: i }), edge: i === s.wfSel ? 'var(--accent,#3D6BFF)' : 'transparent', bg: i === s.wfSel ? 'rgba(255,255,255,0.04)' : 'transparent', c: i === s.wfSel ? '#E9EBF2' : '#9AA1B2', name: w.name, meta: w.meta }));
  const wfStages = wf.stages.map((st, i) => {
    const done = i < wf.active, run = i === wf.active;
    return {
      n: String(i + 1).padStart(2, '0'), name: st[0], owner: st[1],
      status: done ? 'COMPLETE' : run ? 'RUNNING' : 'PENDING',
      stColor: done ? '#34C579' : run ? '#D19A3F' : '#6B7383',
      dot: done ? '#34C579' : run ? '#D19A3F' : '#3C4150',
      anim: run ? 'pulseDot 1.4s ease-in-out infinite' : 'none',
      bd: run ? 'color-mix(in oklab, var(--accent,#3D6BFF) 55%, #1B2130)' : '#1B2130',
      arrowDisp: i === wf.stages.length - 1 ? 'none' : 'block',
    };
  });
  const wfRuns = [
    ['Q3 win-back wave 2', 'directive · operator', 'RUNNING', '#D19A3F', '21:02', '—'],
    ['June close', 'schedule · monthly', 'COMPLETE', '#34C579', '18:40', '42m'],
    ['Packaging vendor shortlist', 'directive · COO', 'COMPLETE', '#34C579', '16:11', '2h 08m'],
    ['Creator brief — fall drop', 'directive · CMO', 'COMPLETE', '#34C579', '14:56', '1h 12m'],
    ['Latency regression sweep', 'alert · observability', 'FAILED', '#E15A52', '11:23', '18m'],
    ['Dormant accounts export', 'workflow · retention', 'COMPLETE', '#34C579', '09:47', '26m'],
    ['SOC 2 evidence refresh', 'schedule · weekly', 'COMPLETE', '#34C579', '02:00', '51m'],
  ].map((r) => ({ name: r[0], trigger: r[1], status: r[2], stColor: r[3], when: r[4], dur: r[5] }));

  // ── approvals ──
  const apFiltered = s.approvals.filter((a) => s.apRisk === 'all' || a.risk === s.apRisk);
  const apRiskChips = [['all', 'ALL'], ['HIGH', 'HIGH'], ['MED', 'MED'], ['LOW', 'LOW']].map((p) => ({ pick: () => setState({ apRisk: p[0] }), label: p[1], bd: s.apRisk === p[0] ? 'var(--accent,#3D6BFF)' : '#232939', bg: s.apRisk === p[0] ? 'color-mix(in oklab, var(--accent,#3D6BFF) 16%, transparent)' : 'transparent', c: s.apRisk === p[0] ? '#E9EBF2' : '#8B93A7' }));
  const apStats = [
    { label: 'PENDING', value: String(s.approvals.length), sub: 'items', color: '#D19A3F' },
    { label: 'MEDIAN WAIT', value: '38m', sub: 'this week', color: '#E9EBF2' },
    { label: 'AUTO-EXECUTED', value: '96.4%', sub: 'within envelope', color: '#34C579' },
    { label: 'DECLINED · 30D', value: '11', sub: 'of 412 asks', color: '#E9EBF2' },
  ];
  const apRows = apFiltered.map((a) => ({ title: a.title, meta: a.meta, chain: a.chain, amount: a.amount, risk: a.risk, riskColor: riskC[a.risk], riskBg: riskC[a.risk] + '14', riskBd: riskC[a.risk] + '45', approve: () => decide(a.id, true), decline: () => decide(a.id, false) }));

  // ── analytics ──
  const mn = Math.min.apply(null, spend30), mx = Math.max.apply(null, spend30);
  const pts = spend30.map((v, i) => (i * (600 / 29)).toFixed(1) + ',' + (140 - ((v - mn) / (mx - mn)) * 118).toFixed(1));
  const mtd = spend30.reduce((a, b) => a + b, 0);
  const anaCards = [
    { label: 'SPEND · 30D', value: money(mtd), delta: '−4.2% vs prior 30d', deltaColor: '#34C579' },
    { label: 'AVG DAILY', value: money(mtd / 30), delta: 'within budget envelope', deltaColor: '#8B93A7' },
    { label: 'COST / TASK', value: '$0.041', delta: '−11% — routing gains', deltaColor: '#34C579' },
    { label: 'PROJECTED · EOM', value: money(mtd * 1.34), delta: 'cap $78,000', deltaColor: '#D19A3F' },
  ];
  const effMax = Math.max.apply(null, deptTok.map((x) => { const tk = AGENTS.filter((a) => a.department === x.d.id).reduce((t2, a) => t2 + a.tasksCompleted, 0); return tk ? x.tok / tk : 0; }));
  const effRows = deptTok.map((x) => {
    const tasks = AGENTS.filter((a) => a.department === x.d.id).reduce((t2, a) => t2 + a.tasksCompleted, 0);
    const eff = tasks ? x.tok / tasks : 0;
    return { name: x.d.name, color: x.d.color, tokFmt: fk(x.tok), cost: money(x.tok * 0.0009), tasks: fk(tasks), eff: eff.toFixed(1), effW: Math.round(eff / effMax * 100) + '%', effColor: eff / effMax > 0.7 ? '#D19A3F' : '#34C579' };
  });

  // ── models ──
  const modelCards = [
    { name: 'atlas-4-frontier', tag: 'REASONING', tagColor: '#7A9BFF', tagBd: 'rgba(122,155,255,0.4)', desc: 'Executive & VP tier reasoning, planning, arbitration.', ctx: '400K', latency: '2.4s', cost: '$9.80', share: '18%', shareW: '18%' },
    { name: 'nova-2-fast', tag: 'WORKER', tagColor: '#34C579', tagBd: 'rgba(52,197,121,0.4)', desc: 'High-volume worker execution: drafts, checks, routing.', ctx: '128K', latency: '380ms', cost: '$0.55', share: '61%', shareW: '61%' },
    { name: 'cirrus-embed-3', tag: 'MEMORY', tagColor: '#A78BFA', tagBd: 'rgba(167,139,250,0.4)', desc: 'Embeddings for governed memory and semantic recall.', ctx: '8K', latency: '45ms', cost: '$0.02', share: '14%', shareW: '14%' },
    { name: 'skylize-slm-1', tag: 'ON-PREM', tagColor: '#D19A3F', tagBd: 'rgba(209,154,63,0.4)', desc: 'Residency-pinned SLM for PII and compliance-sensitive tasks.', ctx: '32K', latency: '210ms', cost: 'flat', share: '7%', shareW: '7%' },
  ];
  const routeRows = [
    { cls: 'Strategic reasoning', model: 'atlas-4-frontier', fallback: 'nova-2-fast', note: 'EXEC/VP tier only · budget-gated per directive' },
    { cls: 'Worker execution', model: 'nova-2-fast', fallback: 'atlas-4-frontier', note: 'Escalates on 2 failed QC passes' },
    { cls: 'Memory & recall', model: 'cirrus-embed-3', fallback: '—', note: 'All memory.search calls · consent-gated' },
    { cls: 'PII / compliance', model: 'skylize-slm-1', fallback: 'human queue', note: 'Never leaves EU-CENTRAL · WORM logged' },
    { cls: 'Vision & assets', model: 'nova-2-fast', fallback: 'atlas-4-frontier', note: 'Creative dept · style-gate before publish' },
  ];

  // ── knowledge ──
  const kbRows = [
    { name: 'Orders warehouse (Postgres)', type: 'DATABASE', docs: '2.4M', fresh: '2 min', freshColor: '#34C579', cov: '100%', covW: '100%', status: 'SYNCED', stColor: '#34C579' },
    { name: 'CRM accounts & pipeline', type: 'CONNECTOR', docs: '184K', fresh: '11 min', freshColor: '#34C579', cov: '98%', covW: '98%', status: 'SYNCED', stColor: '#34C579' },
    { name: 'Contracts vault (S3)', type: 'OBJECT STORE', docs: '31K', fresh: '1 h', freshColor: '#34C579', cov: '96%', covW: '96%', status: 'SYNCED', stColor: '#34C579' },
    { name: 'Support transcripts', type: 'STREAM', docs: '912K', fresh: 'live', freshColor: '#34C579', cov: '91%', covW: '91%', status: 'INDEXING', stColor: '#D19A3F' },
    { name: 'Product analytics events', type: 'WAREHOUSE', docs: '48M', fresh: '26 min', freshColor: '#D19A3F', cov: '84%', covW: '84%', status: 'SYNCED', stColor: '#34C579' },
    { name: 'Brand & policy wiki', type: 'DOCS', docs: '3.1K', fresh: '3 h', freshColor: '#D19A3F', cov: '100%', covW: '100%', status: 'SYNCED', stColor: '#34C579' },
  ];
  const kbStats = [
    { label: 'SOURCES', value: '6', sub: 'governed', color: '#E9EBF2' },
    { label: 'OBJECTS INDEXED', value: '51.5M', sub: 'total', color: '#E9EBF2' },
    { label: 'RECALL P95', value: '212ms', sub: 'memory.search', color: '#E9EBF2' },
    { label: 'CONSENT GATE', value: 'PASS', sub: 'privacy verified', color: '#34C579' },
  ];

  // ── integrations ──
  const integDefs = [
    ['Slack', 'MESSAGING', 'chat.post, approvals.notify — scoped to #ops-command', true],
    ['Salesforce', 'CRM', 'crm.read, pipeline.read — read-only replica', true],
    ['Stripe', 'PAYMENTS', 'billing.read, payouts.read — no write scope', true],
    ['Snowflake', 'WAREHOUSE', 'bi.query via tool proxy — row-level policy', true],
    ['GitHub', 'ENGINEERING', 'repo.read, deploy.dispatch — contract gate', true],
    ['Zendesk', 'SUPPORT', 'tickets.read, tickets.reply — tone-gated', true],
    ['Google Drive', 'DOCS', 'docs.read — brand & policy wiki sync', true],
    ['Shopify', 'COMMERCE', 'catalog.read, orders.read — store ops', false],
    ['HubSpot', 'MARKETING', 'contacts.read, email.send — approval-gated', false],
    ['Jira', 'PLANNING', 'issues.read, issues.create', false],
    ['NetSuite', 'ERP', 'ledger.read — monthly close feed', false],
    ['Teams', 'MESSAGING', 'chat.post — exec digest channel', false],
  ];
  const integPal = ['#7A9BFF', '#34C579', '#A78BFA', '#D19A3F', '#BC6E86', '#5CAD85'];
  const integCards = integDefs.map((x, i) => ({
    name: x[0], cat: x[1], scopes: x[2], mono: x[0].slice(0, 1), mColor: integPal[i % integPal.length],
    stColor: x[3] ? '#34C579' : '#3C4150',
    btnLabel: x[3] ? 'CONFIGURE' : 'CONNECT',
    btnBd: x[3] ? '#262D40' : 'color-mix(in oklab, var(--accent,#3D6BFF) 50%, #262D40)',
    btnBg: x[3] ? 'transparent' : 'color-mix(in oklab, var(--accent,#3D6BFF) 12%, transparent)',
    btnC: x[3] ? '#8B93A7' : '#E9EBF2',
  }));

  // ── developers ──
  const keyRows = [
    { name: 'Production — orchestrator', masked: 'sk-live-••••••••4F2A', created: 'MAR 12', scope: 'FULL', lastUsed: '2 min ago' },
    { name: 'BI read-only', masked: 'sk-live-••••••••C817', created: 'APR 03', scope: 'READ', lastUsed: '1 h ago' },
    { name: 'Staging sandbox', masked: 'sk-test-••••••••90DE', created: 'JUN 21', scope: 'SANDBOX', lastUsed: '3 d ago' },
  ];
  const mColors = { GET: ['#34C579', 'rgba(52,197,121,0.12)'], POST: ['#7A9BFF', 'rgba(122,155,255,0.12)'], PATCH: ['#D19A3F', 'rgba(209,154,63,0.12)'] };
  const endRows = [
    ['POST', '/v1/directives', 'Issue a directive to the organization'],
    ['GET', '/v1/agents', 'List agents, budgets, and status'],
    ['GET', '/v1/runs/:id', 'Inspect a workflow run and its chain'],
    ['POST', '/v1/approvals/:id/decide', 'Approve or decline a pending action'],
    ['GET', '/v1/audit', 'Stream the signed audit log'],
    ['PATCH', '/v1/agents/:id/budget', 'Adjust an agent token ceiling'],
  ].map((e) => ({ method: e[0], path: e[1], desc: e[2], mColor: mColors[e[0]][0], mBg: mColors[e[0]][1] }));
  const hookEvents = ['directive.completed', 'approval.requested', 'approval.decided', 'agent.error', 'budget.threshold', 'run.failed', 'audit.exported', 'policy.violation'].map((n) => ({ name: n }));
  const quickstart = 'curl -X POST https://api.skylize.ai/v1/directives \\\n  -H "Authorization: Bearer $SKYLIZE_KEY" \\\n  -d \'{"text": "Reforecast Q3 revenue"}\'';

  // ── audit log ──
  const actC = { DIRECTIVE: ['#7A9BFF', 'rgba(122,155,255,0.12)'], APPROVAL: ['#D19A3F', 'rgba(209,154,63,0.12)'], TOOL: ['#34C579', 'rgba(52,197,121,0.12)'], SECURITY: ['#E15A52', 'rgba(225,90,82,0.12)'], BILLING: ['#A78BFA', 'rgba(167,139,250,0.12)'] };
  const logChips = [['all', 'ALL'], ['DIRECTIVE', 'DIRECTIVES'], ['APPROVAL', 'APPROVALS'], ['TOOL', 'TOOLS'], ['SECURITY', 'SECURITY'], ['BILLING', 'BILLING']].map((p) => ({ pick: () => setState({ logFilter: p[0] }), label: p[1], bd: s.logFilter === p[0] ? 'var(--accent,#3D6BFF)' : '#232939', bg: s.logFilter === p[0] ? 'color-mix(in oklab, var(--accent,#3D6BFF) 16%, transparent)' : 'transparent', c: s.logFilter === p[0] ? '#E9EBF2' : '#8B93A7' }));
  const logRows = s.audit.filter((l) => s.logFilter === 'all' || l.action === s.logFilter).map((l) => ({ time: l.time, actor: l.actor, action: l.action, actionColor: actC[l.action][0], actionBg: actC[l.action][1], target: l.target, sig: l.sig }));

  // ── security ──
  const secScore = 94, circ = 2 * Math.PI * 56;
  const secControls = [
    ['SSO — SAML 2.0', 'Enforced for all human operators', 'ENFORCED', '#34C579'],
    ['SCIM provisioning', 'Directory-synced roles and deprovisioning', 'ENFORCED', '#34C579'],
    ['Encryption at rest', 'AES-256 · customer-managed keys', 'ENFORCED', '#34C579'],
    ['Data residency', 'Pinned to EU-CENTRAL · no cross-region', 'ENFORCED', '#34C579'],
    ['Human-in-the-loop', 'Required for all HIGH-risk actions', 'ENFORCED', '#34C579'],
    ['PII redaction', 'Applied to logs and learning pipeline', 'ENFORCED', '#34C579'],
    ['Agent sandbox isolation', 'Per-agent tool proxy, zero shared state', 'ENFORCED', '#34C579'],
    ['Quarterly pen test', 'Next window opens JUL 15', 'SCHEDULED', '#D19A3F'],
  ].map((c) => ({ name: c[0], desc: c[1], stLabel: c[2], stColor: c[3] }));
  const secEvents = [
    { text: 'Ad Copy Writer error loop contained — sandbox auto-restarted', time: '2 h', sevColor: '#D19A3F' },
    { text: 'Anomalous token burst flagged on VP Marketing — within ceiling', time: '9 h', sevColor: '#D19A3F' },
    { text: 'SOC 2 evidence refresh completed and signed', time: '1 d', sevColor: '#34C579' },
    { text: '3 stale tool grants revoked by policy sweep', time: '3 d', sevColor: '#34C579' },
  ];
  const secBadges = ['SOC 2 TYPE II', 'ISO 27001', 'GDPR', 'HIPAA-READY'].map((n) => ({ name: n }));

  // ── team ──
  const roleC = { OWNER: ['color-mix(in oklab, var(--accent,#3D6BFF) 80%, #FFFFFF)', 'color-mix(in oklab, var(--accent,#3D6BFF) 16%, transparent)'], ADMIN: ['#A78BFA', 'rgba(167,139,250,0.12)'], OPERATOR: ['#34C579', 'rgba(52,197,121,0.12)'], AUDITOR: ['#D19A3F', 'rgba(209,154,63,0.12)'] };
  const memberRows = [
    ['Mara Lindqvist', 'mara@aventra.eu', 'OWNER', '2 min ago', 'ON'],
    ['Deniz Aksoy', 'deniz@aventra.eu', 'ADMIN', '18 min ago', 'ON'],
    ['Jonas Weber', 'jonas@aventra.eu', 'OPERATOR', '1 h ago', 'ON'],
    ['the client contact Raman', 'priya@aventra.eu', 'OPERATOR', '3 h ago', 'ON'],
    ['Sofia Marino', 'sofia@aventra.eu', 'AUDITOR', '1 d ago', 'ON'],
    ['External audit — KPMG', 'audit-ext@aventra.eu', 'AUDITOR', '6 d ago', 'OFF'],
  ].map((m) => ({ initials: m[0].split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase(), name: m[0], email: m[1], role: m[2], roleColor: roleC[m[2]][0], roleBg: roleC[m[2]][1], last: m[3], mfa: m[4], mfaColor: m[4] === 'ON' ? '#34C579' : '#E15A52' }));
  const permDefs = [
    ['Issue directives', [1, 1, 1, 0]],
    ['Approve HIGH-risk actions', [1, 1, 0, 0]],
    ['Manage agents & budgets', [1, 1, 0, 0]],
    ['Manage integrations & keys', [1, 1, 0, 0]],
    ['Read audit log', [1, 1, 1, 1]],
    ['Billing & plan', [1, 0, 0, 0]],
  ];
  const permRows = permDefs.map((p) => ({ name: p[0], cells: p[1].map((on) => ({ bg: on ? 'var(--accent,#3D6BFF)' : '#232939', sh: on ? '0 0 8px color-mix(in oklab, var(--accent,#3D6BFF) 60%, transparent)' : 'none' })) }));

  // ── billing ──
  const meters = [
    { label: 'TOKENS · ANNUAL COMMIT', used: '418M', cap: '675M', w: '62%', color: 'var(--accent,#3D6BFF)' },
    { label: 'OPERATOR SEATS', used: '6', cap: '25', w: '24%', color: '#34C579' },
    { label: 'AGENT SLOTS', used: '151', cap: '250', w: '60%', color: '#A78BFA' },
  ];
  const invoices = [
    { id: 'INV-2026-06', period: 'JUN 01 — JUN 30', amount: '$58,410', status: 'PAID', stColor: '#34C579' },
    { id: 'INV-2026-05', period: 'MAY 01 — MAY 31', amount: '$61,275', status: 'PAID', stColor: '#34C579' },
    { id: 'INV-2026-04', period: 'APR 01 — APR 30', amount: '$54,890', status: 'PAID', stColor: '#34C579' },
    { id: 'INV-2026-03', period: 'MAR 01 — MAR 31', amount: '$49,320', status: 'PAID', stColor: '#34C579' },
  ];

  // ── settings ──
  const autonomyDescs = [
    'L0 — Observe only. Every action requires human sign-off.',
    'L1 — Draft. Agents prepare work; humans execute.',
    'L2 — Execute within budget. HIGH-risk actions escalate to you.',
    'L3 — Execute + reallocate budgets. Only irreversible actions escalate.',
    'L4 — Full autonomy inside the governance envelope. Audit everything.',
  ];
  const autonomyChips = [0, 1, 2, 3, 4].map((i) => ({ pick: () => set({ autonomy: i }), label: 'L' + i, bd: s.autonomy === i ? 'var(--accent,#3D6BFF)' : '#232939', bg: s.autonomy === i ? 'color-mix(in oklab, var(--accent,#3D6BFF) 18%, transparent)' : 'transparent', c: s.autonomy === i ? '#E9EBF2' : '#8B93A7' }));
  const guardDefs = [
    ['cap', 'Approval above $10,000', 'Any single commitment over the cap escalates to a human'],
    ['email', 'Block external sends', 'Outbound email & posts require the brand gate + approval'],
    ['pii', 'Redact PII in logs', 'Personally identifiable data masked before storage'],
    ['fallback', 'Silent model fallback', 'Allow automatic downgrade to nova-2 on atlas-4 saturation'],
  ];
  const guardrails = guardDefs.map((g) => {
    const on = !!s.guards[g[0]];
    return { name: g[1], desc: g[2], toggle: () => { const guards = { ...s.guards, [g[0]]: !on }; set({ guards }); }, trackBg: on ? 'var(--accent,#3D6BFF)' : '#141826', trackBd: on ? 'color-mix(in oklab, var(--accent,#3D6BFF) 70%, transparent)' : '#232939', knobX: on ? '17px' : '2px' };
  });

  const notifItems = [
    { dot: '#D19A3F', text: 'AP-2216 waiting 14 min — Q3 paid-social budget', when: '14m' },
    { dot: '#E15A52', text: 'Ad Copy Writer entered error state — auto-restart armed', when: '2h' },
    { dot: '#34C579', text: 'June close completed and signed by CFO chain', when: '4h' },
    { dot: '#7A9BFF', text: 'Routing gains: cost per task down 11% this week', when: '1d' },
  ];

  return {
    accentVar: acc,
    crumb: crumbs[s.screen] || 'overview',
    activeCount: String(active), tokRateFmt: fk(s.tokRate), costToday: money(cost), clock: s.clock,
    toggleNotif: () => setState({ notifOpen: !s.notifOpen }), notifOpen: s.notifOpen, notifItems,
    goChat: () => nav('chat'), goApprovals: () => nav('appr'), goAnalytics: () => nav('ana'),
    navGroups, labelDisp: open ? 'block' : 'none', sidebarW: open ? '198px' : '52px',
    toggleSidebar: () => set({ sidebarOpen: !open }), chevRot: open ? '180deg' : '0deg',
    // dashboard
    isDash: s.screen === 'dash', dirInput: s.dirInput,
    onDirInput: (e) => setState({ dirInput: e.target.value }),
    dirKey: (e) => { if (e.key === 'Enter') { e.preventDefault(); const tx = s.dirInput; set({ screen: 'chat' }); doSend(tx); } },
    sendDir: () => { const tx = s.dirInput; set({ screen: 'chat' }); doSend(tx); },
    heroStackDisp: 'block', tierLayers, kpis, tiltMove, tiltLeave,
    deptBars, apPrevN: String(s.approvals.length), apPrev, pulseLogs,
    // chat
    isChat: s.screen === 'chat', chatRef: chatElRef, chatEmpty: s.messages.length === 0 && !s.chatBusy,
    suggestions: sugg.map((txt) => ({ text: txt, send: () => doSend(txt) })),
    messagesVm, chatBusy: s.chatBusy, chatPhase: s.chatPhase,
    chatInput: s.chatInput, onChatInput: (e) => setState({ chatInput: e.target.value }),
    chatKey: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(s.chatInput); } },
    sendChat: () => doSend(s.chatInput), sendOpacity: s.chatInput.trim() ? '1' : '0.55',
    railAgents, railEmpty: s.rail.length === 0, railCount: s.rail.length ? s.rail.length + ' ACTIVE' : '—',
    chatTokFmt: fk(s.sessTok), chatCostFmt: '$' + (s.sessTok * 0.0009).toFixed(2),
    fileInputRef: fileInputElRef, onAttachClick, onFileChange,
    composerChipsShow: composerChips.length > 0, composerChips,
    toggleTagMenu, tagMenuOpen: s.tagMenuOpen, tagQ: s.tagQ, onTagQ, tagOptions,
    toggleConnMenu, connMenuOpen: s.connMenuOpen, connOptions,
    tagBtnBd: s.chatTags.length ? 'color-mix(in oklab, var(--accent,#3D6BFF) 55%, #232939)' : 'transparent',
    tagBtnBg: s.tagMenuOpen ? 'rgba(255,255,255,0.05)' : 'transparent',
    tagBtnC: s.chatTags.length || s.tagMenuOpen ? 'var(--accent,#3D6BFF)' : '#8B93A7',
    connBtnBd: s.chatConnectors.length ? 'color-mix(in oklab, var(--accent,#3D6BFF) 55%, #232939)' : 'transparent',
    connBtnBg: s.connMenuOpen ? 'rgba(255,255,255,0.05)' : 'transparent',
    connBtnC: s.chatConnectors.length || s.connMenuOpen ? 'var(--accent,#3D6BFF)' : '#8B93A7',
    // starmap
    isStar: s.screen === 'star',
    starDeptName: sdept.name, starTagline: sdept.tagline, starColor: sdept.color,
    starGlow: 'color-mix(in oklab, ' + sdept.color + ' 42%, transparent)',
    starIdxLabel: String(starIdx + 1).padStart(2, '0'),
    starPrev: () => starStep(-1), starNext: () => starStep(1),
    starTilt, starTiltReset, starTiltRef: starTiltElRef, starPlaneRef: starPlaneElRef,
    starNodes, starLines, starDots, starList,
    starEmpty: smembers.length === 0,
    starQ: s.starQ, onStarQ: (e) => setState({ starQ: e.target.value }),
    starAgents: String(smembers.length), starExecN: String(starExecN),
    starTokFmt: fk(smembers.reduce((tt, a) => tt + a.tokensUsed, 0)), starHead: shead ? shead.name : 'CEO',
    hasSel: !!selA, sel, closeSel: () => setState({ selId: null }),
    // workflows
    isWf: s.screen === 'wf', wfList, wfSel: { name: wf.name, meta: wf.meta, desc: wf.desc }, wfStages, wfRuns,
    // approvals
    isAppr: s.screen === 'appr', apRiskChips, apStats, apRows, apEmpty: apFiltered.length === 0,
    // analytics
    isAna: s.screen === 'ana', anaCards, spendPts: pts.join(' '), spendArea: '0,140 ' + pts.join(' ') + ' 600,140', spendPeak: money(mx), effRows,
    // models
    isMod: s.screen === 'mod', modelCards, routeRows,
    // knowledge
    isKb: s.screen === 'kb', kbTotalDocs: '51.5M', kbStats, kbRows,
    // integrations
    isInteg: s.screen === 'integ', integConnN: String(integDefs.filter((x) => x[3]).length), integCards,
    // developers
    isDev: s.screen === 'dev', keyRows, endRows, hookEvents, quickstart,
    // audit
    isLog: s.screen === 'log', logChips, logRows,
    // security
    isSec: s.screen === 'sec', secBadges, secDash: (circ * secScore / 100).toFixed(1) + ' ' + circ.toFixed(1), secScore: String(secScore), secControls, secEvents,
    // team
    isTeam: s.screen === 'team', memberRows, permRows,
    // billing
    isBill: s.screen === 'bill', planRenews: 'AUG 01', spendMTD: '$41,900', forecastFmt: '$63,400', meters, invoices,
    // settings
    isSet: s.screen === 'set', orgName: s.orgName, onOrgName: (e) => set({ orgName: e.target.value }),
    region: s.region, onRegion: (e) => set({ region: e.target.value }),
    retention: s.retention, onRetention: (e) => set({ retention: e.target.value }),
    autonomyDesc: autonomyDescs[s.autonomy], autonomyChips, guardrails,
    pauseTitle: s.pausedAll ? 'Agents paused' : 'Pause all agents',
    pauseLabel: s.pausedAll ? 'RESUME' : (s.pauseArm ? 'CONFIRM PAUSE?' : 'PAUSE ALL'),
    pauseBg: s.pausedAll || s.pauseArm ? 'rgba(225,90,82,0.16)' : 'transparent', pauseC: '#E15A52',
    dangerPause,
    toastShow: !!s.toast, toastText: s.toast ? s.toast.text : '', toastDot: s.toast ? s.toast.dot : '#34C579',
  };
}
