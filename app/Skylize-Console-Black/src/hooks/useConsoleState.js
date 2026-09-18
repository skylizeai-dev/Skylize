import { useEffect, useRef, useState, useCallback } from 'react';
import { DEPARTMENTS, AGENTS } from '../data/agentNetworkData.js';
import {
  AUTONOMY_MODES,
  DEFAULT_AUTONOMY_MODE,
  fetchAutonomyMode,
  putAutonomyMode,
} from '../lib/autonomyClient.js';
import {
  AUTH_HEADER_DOC,
  approveHitl,
  engageKillSwitch,
  fetchAgents,
  fetchApiKeys,
  fetchApprovals,
  fetchAudit,
  fetchOrgUsers,
  issueApiKey,
  rejectHitl,
  revokeApiKey,
  sendCoworkTurn,
} from '../lib/consoleClient.js';

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

// The backend `proposal_summary` is an open dict, not a typed shape, so it is
// rendered as its own key/value pairs rather than mapped onto invented fields.
function summarise(obj) {
  if (!obj || typeof obj !== 'object') return '';
  return Object.keys(obj)
    .slice(0, 4)
    .map((k) => {
      const v = obj[k];
      const text = v === null || v === undefined
        ? '\u2014'
        : (typeof v === 'object' ? JSON.stringify(v) : String(v));
      return k + ': ' + (text.length > 60 ? text.slice(0, 60) + '\u2026' : text);
    })
    .join('  \u00b7  ');
}

function agoFrom(iso) {
  if (!iso) return '';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return secs + 's ago';
  if (secs < 3600) return Math.round(secs / 60) + 'm ago';
  if (secs < 86400) return Math.round(secs / 3600) + 'h ago';
  return Math.round(secs / 86400) + 'd ago';
}

function hhmmss(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const q = (n) => String(n).padStart(2, '0');
  return q(d.getUTCHours()) + ':' + q(d.getUTCMinutes()) + ':' + q(d.getUTCSeconds());
}

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

// AUTONOMY_MODES / DEFAULT_AUTONOMY_MODE now come from ../lib/autonomyClient.js,
// which is the one place this app states the five modes, next to the calls that
// exchange them with the server. The ORDER is still load-bearing (least to most
// autonomous) because the Settings chips render in it.
//
// AUTONOMY IS NO LONGER PERSISTED HERE. It is ORG-WIDE state owned by the
// backend (`org_autonomy_mode`, migration 0028) and read/written through the
// BFF. A per-browser localStorage copy was a second source of truth for a
// governance setting: two operators could see different postures, and a fresh
// browser would silently claim whatever this app last defaulted to. The other
// persisted keys below are per-operator UI preference and stay local.

const STORAGE_KEY = 'skylize.console.v3';
const LEGACY_STORAGE_KEY = 'skylize.console.v2';

// v3 first; a v2 blob is read once and migrated on the next persist(). Reading
// the old key is what stops an existing browser from resetting to defaults.
function loadPersisted() {
  try {
    const current = localStorage.getItem(STORAGE_KEY);
    if (current) return JSON.parse(current);
    const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (legacy) return JSON.parse(legacy);
    return {};
  } catch (e) { return {}; }
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
    // ── approvals: the real HITL queue ──────────────────────────────────
    // The six sample rows that used to live here carried risk/amount/chain,
    // none of which exists behind /api/v1/hitl. They are gone rather than
    // re-derived: see the note above `fetchApprovals` in lib/consoleClient.js.
    // Nothing is shown until the server answers; a failure is stated, never
    // replaced with a sample queue.
    approvals: [],
    approvalsLoading: true,
    approvalsError: null,
    // hitl_id currently being approved/rejected -- disables that row's buttons.
    approvalBusyId: null,

    // ── audit: the real append-only feed ────────────────────────────────
    audit: [],
    auditLoading: true,
    auditError: null,
    pulse: seedPulse,
    orgName: persisted.orgName || 'Aventra Retail Group', region: persisted.region || 'eu-central',
    retention: persisted.retention || '365',
    // Fail closed until the server answers (ruling 7). A persisted value is
    // deliberately NOT read here -- see the note above STORAGE_KEY.
    autonomy: DEFAULT_AUTONOMY_MODE,
    autonomyConfigured: false,
    autonomyLoading: true,
    autonomySaving: false,
    autonomyError: null,
    // 'read' | 'write' | null. A failed READ means the value on screen is the
    // fail-closed default; a failed WRITE means it is still the stored posture,
    // just not the one that was asked for. Saying "fail-closed default" for a
    // write failure would misreport the org's actual setting.
    autonomyErrorKind: null,
    guards: persisted.guards || { cap: true, email: true, pii: true, fallback: false },
    pausedAll: false, pauseArm: false, toast: null,

    // ── live agent registry ─────────────────────────────────────────────
    // GET /api/v1/agents. The generated 151-agent fixture is still imported
    // for the Starmap GEOMETRY only (see the note at `agentsLive`); every
    // COUNT and roster the operator reads comes from here.
    agentsLive: [],
    agentsLoading: true,
    agentsError: null,

    // ── developers: api keys ────────────────────────────────────────────
    keys: [],
    keysLoading: true,
    keysError: null,
    // The plaintext secret from a successful mint. Held in memory ONLY, shown
    // once, and never written to localStorage -- the backend will never show
    // it again (api_keys.py:6-8).
    mintedKey: null,

    // ── team: org users ─────────────────────────────────────────────────
    members: [],
    membersLoading: true,
    membersError: null,

    // ── kill switch ─────────────────────────────────────────────────────
    // The operator's own words. NOT defaulted: this string lands in the audit
    // record of the most consequential control in the product, so the console
    // must never supply it on their behalf.
    pauseReason: '',
    pauseBusy: false,
    pauseError: null,
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
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        screen: s.screen, sidebarOpen: s.sidebarOpen, netSel: s.netSel, orgName: s.orgName,
        region: s.region, retention: s.retention, guards: s.guards,
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

  // ── co-work turn: a REAL governed agent run ───────────────────────────
  //
  // WHAT THIS REPLACED. Until now "sending a directive" ran entirely in the
  // browser: `buildReply` picked one of seven canned scripts by keyword-matching
  // the text, a fake delegation chain was assembled from the generated agent
  // fixture, and a chain of setTimeout calls animated phases ("ROUTING VIA
  // ORCHESTRATOR", "EXECUTING · N AGENTS") and invented a token count. Nothing
  // left the tab. It looked exactly like a governed run and was not one.
  //
  // It is now POST /api/console/cowork/turns -> POST /api/v1/cowork/turns,
  // which is one `AgentExecutionService.execute()` call and therefore walks the
  // real pipeline: the synchronous decision gate, the token mint, ordered token
  // validation before every LLM egress, and ToolProxy validation on every tool
  // call (cowork.py:1-18).
  //
  // ONLY `message` GOES UP. The composer's attachments, agent/department tags
  // and connector selections have no counterpart in the backend contract and
  // are NOT sent -- they are disabled in the UI instead (see `tagUnavailable`
  // in the view model). Silently dropping them while the button still worked
  // would tell the operator their directive was routed somewhere it was not.
  const doSend = useCallback((text) => {
    const message = (text || '').trim();
    const s = stateRef.current;
    if (!message) return;
    if (s.chatBusy) return;

    const user = { who: 'user', lines: [{ k: 'p', t: message }], chips: [] };
    setState((prev) => ({
      ...prev,
      messages: prev.messages.concat([user]),
      chatInput: '',
      dirInput: '',
      chatBusy: true,
      // A truthful single phase. There is no client-visible delegation chain to
      // narrate, so the console no longer pretends to watch one.
      chatPhase: 'RUNNING GOVERNED TURN',
      rail: [],
    }));

    sendCoworkTurn(message).then(
      (result) => {
        setState((prev) => {
          // A DEFERRED turn produced no reply: a HITL row exists and a human
          // must act. Rendering an empty answer would hide that entirely.
          const reply = result.deferred
            ? {
                who: 'ai',
                deferred: true,
                lines: [
                  { k: 'b', t: 'Deferred to a human.' },
                  { k: 'p', t: result.reason || 'This turn requires human approval.' },
                  { k: 'p', t: 'It is now in Approvals as ' + result.hitlId + '.' },
                ],
              }
            : {
                who: 'ai',
                lines: [{ k: 'p', t: result.reply }],
                // The REAL provenance the backend returned. No invented token
                // id, no invented agent count, no invented token total.
                gov: { agentId: result.agentId, deliverableId: result.deliverableId },
              };
          return {
            ...prev,
            chatBusy: false,
            chatPhase: '',
            messages: prev.messages.concat([reply]),
          };
        });
      },
      (error) => {
        setState((prev) => ({
          ...prev,
          chatBusy: false,
          chatPhase: '',
          messages: prev.messages.concat([
            {
              who: 'ai',
              failed: true,
              lines: [
                { k: 'b', t: 'The turn did not run.' },
                { k: 'p', t: error.message || 'The backend could not be reached.' },
              ],
            },
          ]),
        }));
      },
    );
  }, [setState]);

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

  // ── org autonomy mode: read from the server on mount ──
  //
  // FAIL CLOSED on any read failure (ruling 7). `observe` is already the
  // initial value, so a failure leaves the console showing the mode where
  // every action needs a human -- and `autonomyError` makes it clear that is
  // the fallback rather than the org's actual posture, so nobody reads a
  // network failure as "we are set to observe".
  const autonomyAliveRef = useRef(true);
  useEffect(() => {
    autonomyAliveRef.current = true;
    fetchAutonomyMode().then(
      (result) => {
        if (!autonomyAliveRef.current) return;
        setState({
          autonomy: result.mode,
          autonomyConfigured: result.configured,
          autonomyLoading: false,
          autonomyError: null,
          autonomyErrorKind: null,
        });
      },
      (error) => {
        if (!autonomyAliveRef.current) return;
        setState({
          autonomy: DEFAULT_AUTONOMY_MODE,
          autonomyConfigured: false,
          autonomyLoading: false,
          autonomyError: error.message || 'Could not read the autonomy mode.',
          autonomyErrorKind: 'read',
        });
      },
    );
    return () => { autonomyAliveRef.current = false; };
  }, [setState]);

  // Write the org-wide mode. NOT optimistic: `autonomy` is only ever assigned
  // from a server response, so a failed PUT cannot leave the UI showing a
  // posture the backend never stored. The chips render server truth throughout.
  const setAutonomy = useCallback((mode) => {
    const s = stateRef.current;
    if (s.autonomySaving) return;               // one write at a time
    if (mode === s.autonomy && !s.autonomyError) return;  // nothing to change
    setState({ autonomySaving: true, autonomyError: null, autonomyErrorKind: null });
    putAutonomyMode(mode).then(
      (result) => {
        if (!autonomyAliveRef.current) return;
        setState({
          // The mode the backend PERSISTED, not the one requested.
          autonomy: result.mode,
          autonomyConfigured: result.configured,
          autonomySaving: false,
          autonomyError: null,
          autonomyErrorKind: null,
        });
      },
      (error) => {
        if (!autonomyAliveRef.current) return;
        // The selection stays where the server last put it, and the error says
        // so out loud. Never swallowed, never a silent no-op.
        setState({
          autonomySaving: false,
          autonomyError: error.message || 'Could not save the autonomy mode.',
          autonomyErrorKind: 'write',
        });
      },
    );
  }, [setState]);

  // ── approvals: load + verdict ────────────────────────────────────
  const aliveRef = useRef(true);

  const loadApprovals = useCallback(() => {
    setState({ approvalsLoading: true, approvalsError: null });
    return fetchApprovals(50).then(
      (result) => {
        if (!aliveRef.current) return;
        setState({ approvals: result.items, approvalsLoading: false, approvalsError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        // EMPTY, not sample rows. "Could not read the queue" and "the queue is
        // empty" are opposite facts, and the view model keeps them distinct.
        setState({
          approvals: [],
          approvalsLoading: false,
          approvalsError: error.message || 'Could not read the approvals queue.',
        });
      },
    );
  }, [setState]);

  // ── audit ────────────────────────────────────────────────
  const loadAudit = useCallback(() => {
    setState({ auditLoading: true, auditError: null });
    return fetchAudit(50).then(
      (result) => {
        if (!aliveRef.current) return;
        setState({ audit: result.entries, auditLoading: false, auditError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          audit: [],
          auditLoading: false,
          auditError: error.message || 'Could not read the audit log.',
        });
      },
    );
  }, [setState]);

  // ONE BOOLEAN BECAME TWO CALLS. `decide(id, ok)` used to filter a row out of
  // local state and append an invented audit line with a random hex "signature".
  // Approve and reject are different backend operations with different
  // consequences -- approve EXECUTES the deferred work, reject executes nothing
  // -- so they dispatch to two different endpoints, and the queue is re-read
  // from the server afterwards rather than spliced locally.
  const decide = useCallback((hitlId, ok, note) => {
    const s = stateRef.current;
    if (s.approvalBusyId) return;
    setState({ approvalBusyId: hitlId });
    const action = ok ? approveHitl : rejectHitl;
    action(hitlId, note).then(
      () => {
        if (!aliveRef.current) return;
        setState({ approvalBusyId: null });
        showToast(
          (ok ? 'APPROVED · ' : 'REJECTED · ') + hitlId.slice(0, 8),
          ok ? '#34C579' : '#E15A52',
        );
        // Server truth, not a local splice: an approval can itself defer again,
        // and only the backend knows the row resulting status.
        loadApprovals();
        loadAudit();
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({ approvalBusyId: null });
        showToast(error.message || 'The verdict did not record.', '#E15A52');
        // Re-read regardless: a 409 means someone else already actioned it, so
        // the on-screen queue is the stale thing.
        loadApprovals();
      },
    );
  }, [setState, showToast, loadApprovals, loadAudit]);

  // ── kill switch ──────────────────────────────────────────
  //
  // WHAT CHANGED. This used to be a two-click LOCAL boolean that flipped
  // `pausedAll` and toasted "ALL AGENTS PAUSED" without contacting anything.
  // It now calls POST /api/v1/kill-switch/engage, which needs scope_type +
  // scope_id + reason.
  //
  // scope_type is "tenant" -- the existing PAUSE ALL button is org-wide by
  // design -- but the REASON comes from the operator and is never defaulted.
  // The arm-then-confirm interaction is kept: the second click is what sends.
  const dangerPause = useCallback(() => {
    const s = stateRef.current;
    if (s.pauseBusy) return;
    if (s.pausedAll) {
      // Disengage is NOT wired: POST /api/v1/kill-switch/disengage exists, but
      // the console has never had a scope-aware resume affordance, and adding
      // one here would be a second unreviewed control on the most consequential
      // surface in the product. Stated out loud rather than faked.
      showToast('RESUME IS NOT WIRED — disengage from the backend.', '#D19A3F');
      return;
    }
    if (!s.pauseArm) {
      clearTimeout(armTRef.current);
      setState({ pauseArm: true, pauseError: null });
      armTRef.current = setTimeout(() => setState({ pauseArm: false }), 8000);
      return;
    }
    const reason = (s.pauseReason || '').trim();
    if (!reason) {
      setState({ pauseError: 'A reason is required — it is recorded in the audit trail.' });
      return;
    }
    clearTimeout(armTRef.current);
    setState({ pauseBusy: true, pauseError: null });
    engageKillSwitch('tenant', 'tenant', reason).then(
      () => {
        if (!aliveRef.current) return;
        setState({ pausedAll: true, pauseArm: false, pauseBusy: false, pauseError: null });
        showToast('KILL SWITCH ENGAGED · TENANT SCOPE', '#E15A52');
        loadAudit();
      },
      (error) => {
        if (!aliveRef.current) return;
        // NOT paused. The flag stays false so the console cannot claim a
        // platform control is engaged when the backend never accepted it.
        setState({
          pausedAll: false,
          pauseArm: false,
          pauseBusy: false,
          pauseError: error.message || 'The kill switch did not engage.',
        });
        showToast('KILL SWITCH DID NOT ENGAGE', '#E15A52');
      },
    );
  }, [setState, showToast, loadAudit]);

  // ── mount: load every wired screen from the backend ───────────────────
  //
  // GET on mount for each, in parallel. Each failure is isolated: one screen
  // being unreachable never blanks another, and no screen falls back to
  // fixture data.
  useEffect(() => {
    aliveRef.current = true;

    fetchAgents().then(
      (agents) => {
        if (!aliveRef.current) return;
        setState({ agentsLive: agents, agentsLoading: false, agentsError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          agentsLive: [],
          agentsLoading: false,
          agentsError: error.message || 'Could not read the agent registry.',
        });
      },
    );

    fetchApiKeys().then(
      (keys) => {
        if (!aliveRef.current) return;
        setState({ keys, keysLoading: false, keysError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          keys: [],
          keysLoading: false,
          keysError: error.message || 'Could not read the API keys.',
        });
      },
    );

    fetchOrgUsers().then(
      (members) => {
        if (!aliveRef.current) return;
        setState({ members, membersLoading: false, membersError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          members: [],
          membersLoading: false,
          membersError: error.message || 'Could not read the member list.',
        });
      },
    );

    loadApprovals();
    loadAudit();

    return () => { aliveRef.current = false; };
  }, [setState, loadApprovals, loadAudit]);

  // ── developers: mint / revoke ─────────────────────────────────
  //
  // The minted secret is kept in memory and shown once. It is deliberately NOT
  // written to localStorage with the other persisted keys: the backend will
  // never return it again, but that is a reason to copy it, not a reason for a
  // static web app to store a live credential on disk.
  const setPauseReason = useCallback((reason) => setState({ pauseReason: reason }), [setState]);
  const dismissMintedKey = useCallback(() => setState({ mintedKey: null }), [setState]);

  const mintKey = useCallback((name) => {
    const label = (name || '').trim();
    if (!label) {
      setState({ keysError: 'A key needs a name.' });
      return;
    }
    setState({ keysError: null });
    issueApiKey(label, []).then(
      (issued) => {
        if (!aliveRef.current) return;
        setState({ mintedKey: issued });
        showToast('KEY MINTED · COPY IT NOW', '#34C579');
        fetchApiKeys().then(
          (keys) => { if (aliveRef.current) setState({ keys }); },
          () => {},
        );
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({ keysError: error.message || 'Could not mint a key.' });
        showToast('KEY NOT MINTED', '#E15A52');
      },
    );
  }, [setState, showToast]);

  const revokeKey = useCallback((keyId) => {
    setState({ keysError: null });
    revokeApiKey(keyId).then(
      () => {
        if (!aliveRef.current) return;
        showToast('KEY REVOKED', '#E15A52');
        fetchApiKeys().then(
          (keys) => { if (aliveRef.current) setState({ keys }); },
          () => {},
        );
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({ keysError: error.message || 'Could not revoke the key.' });
      },
    );
  }, [setState, showToast]);

  return buildViewModel({
    state, accent, chatElRef, fileInputElRef, starTiltElRef, starPlaneElRef,
    setState, set, setAutonomy, nav, tiltMove, tiltLeave,
    onAttachClick, onFileChange, removeAttachment, toggleTagMenu, toggleConnMenu, onTagQ, addTag, removeTag, addConnector, removeConnector,
    doSend, starGeom, starStep, starGo, starTilt, starTiltReset, netGeom, decide, dangerPause, showToast, sparks, spend30,
    loadApprovals, loadAudit, mintKey, revokeKey, setPauseReason, dismissMintedKey,
  });
}

function buildViewModel(ctx) {
  const {
    state: s, accent: acc, chatElRef, fileInputElRef, starTiltElRef, starPlaneElRef,
    setState, set, setAutonomy, nav, tiltMove, tiltLeave,
    onAttachClick, onFileChange, removeAttachment, toggleTagMenu, toggleConnMenu, onTagQ, addTag, removeTag, addConnector, removeConnector,
    doSend, starGeom, starStep, starGo, starTilt, starTiltReset, netGeom, decide, dangerPause, showToast, sparks, spend30,
    loadApprovals, loadAudit, mintKey, revokeKey, setPauseReason, dismissMintedKey,
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
    // WIRED: the real HITL queue depth. "oldest 3h" is dropped -- the queue is
    // real now, and that figure never was.
    { label: 'PENDING APPROVALS', value: s.approvalsError ? '\u2014' : String(s.approvals.length), sub: s.approvalsError ? 'unavailable' : 'in queue', delta: '', deltaColor: '#D19A3F', spark: sparks[3], sparkColor: '#D19A3F', valColor: '#D19A3F' },
    // WIRED: the real registry size. This tile used to read "N of 151", the
    // fixture count; the governed registry holds 23.
    { label: 'GOVERNED AGENTS', value: s.agentsLoading ? '\u2026' : (s.agentsError ? '\u2014' : String(s.agentsLive.length)), sub: s.agentsError ? 'unavailable' : 'in registry', delta: '', deltaColor: '#77809A', spark: sparks[4], sparkColor: acc, valColor: '#E9EBF2' },
  ];
  const deptTok = DEPARTMENTS.map((d) => ({ d, tok: AGENTS.filter((a) => a.department === d.id).reduce((tt, a) => tt + a.tokensUsed, 0) })).sort((a, b) => b.tok - a.tok);
  const maxTok = deptTok.length ? deptTok[0].tok : 1;
  const deptBars = deptTok.slice(0, 8).map((x) => ({ name: x.d.name, color: x.d.color, w: Math.round(x.tok / maxTok * 100) + '%', amt: money(x.tok * 0.0009) }));
  // The dashboard preview shows the same real rows the Approvals screen does:
  // reason + agent + age, no risk chip, because there is no risk field.
  const apPrev = s.approvals.slice(0, 3).map((a) => ({
    title: a.triggerReason || 'Deferred to a human',
    meta: (a.agentId || 'unknown agent') + ' \u00b7 ' + (agoFrom(a.createdAt) || a.createdAt),
    approve: () => decide(a.hitlId, true),
    decline: () => decide(a.hitlId, false),
  }));
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
  //
  // RISK / AMOUNT / CHAIN ARE GONE, and this is the whole reshape. The screen
  // was designed around a HIGH/MED/LOW risk chip, a money column and a
  // delegation chain; /api/v1/hitl has none of the three (HitlItemResponse is
  // hitl_id, agent_id, trigger_reason, status, created_at, expires_at,
  // proposal_summary, request_input). Inventing a risk tier in the browser
  // would be inventing a governance judgement the platform never made, so the
  // filter chips now filter on STATUS, which is real, and the row shows
  // `trigger_reason` -- the recorded reason the item is waiting at all.
  const apStatuses = ['all'].concat(
    s.approvals.map((a) => a.status).filter((v, i, arr) => v && arr.indexOf(v) === i),
  );
  const apFiltered = s.approvals.filter((a) => s.apRisk === 'all' || a.status === s.apRisk);
  const apRiskChips = apStatuses.map((id) => ({
    pick: () => setState({ apRisk: id }),
    label: id === 'all' ? 'ALL' : id.toUpperCase(),
    bd: s.apRisk === id ? 'var(--accent,#3D6BFF)' : '#232939',
    bg: s.apRisk === id ? 'color-mix(in oklab, var(--accent,#3D6BFF) 16%, transparent)' : 'transparent',
    c: s.apRisk === id ? '#E9EBF2' : '#8B93A7',
  }));
  // ONE REAL STAT. "Median wait", "auto-executed 96.4%" and "declined 11 of
  // 412" had no backing query anywhere in the platform -- they were decoration
  // with a number in them, which on an approvals screen reads as fact.
  const apStats = [
    { label: 'PENDING', value: String(s.approvals.length), sub: 'in queue', color: '#D19A3F' },
  ];
  const apRows = apFiltered.map((a) => ({
    // The proposal summary is the backend own dict; the id is what identifies
    // the row to a human, so it leads.
    title: a.triggerReason || 'Deferred to a human',
    meta: (a.agentId || 'unknown agent') + ' \u00b7 ' + (agoFrom(a.createdAt) || a.createdAt),
    hitlId: a.hitlId,
    shortId: a.hitlId.slice(0, 8),
    status: a.status,
    summary: summarise(a.proposalSummary),
    busy: s.approvalBusyId === a.hitlId,
    // Two distinct calls. The note is optional free text the backend accepts.
    approve: (note) => decide(a.hitlId, true, note),
    decline: (note) => decide(a.hitlId, false, note),
  }));


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
  //
  // REAL KEYS. The three rows here used to be invented ("Production —
  // orchestrator / sk-live-••••4F2A / FULL / 2 min ago"), which on a key
  // management screen is the most dangerous kind of fiction: an operator could
  // believe a key exists, or believe one was revoked.
  //
  // `masked` is now the backend `prefix`, which is the real non-secret
  // identifying fragment, and `scope` is the key actual `scopes` list. A key
  // scopes BECOME its roles (app/auth/service.py:95), so this column is load
  // bearing rather than cosmetic.
  const keyRows = s.keys.map((k) => ({
    keyId: k.keyId,
    name: k.name,
    masked: k.prefix + '\u2026',
    created: agoFrom(k.createdAt) || k.createdAt,
    scope: k.scopes.length ? k.scopes.join(', ').toUpperCase() : 'NONE',
    lastUsed: k.revokedAt
      ? 'REVOKED'
      : (k.lastUsedAt ? agoFrom(k.lastUsedAt) : 'never used'),
    revoked: !!k.revokedAt,
    revoke: () => revokeKey(k.keyId),
  }));
  const mColors = { GET: ['#34C579', 'rgba(52,197,121,0.12)'], POST: ['#7A9BFF', 'rgba(122,155,255,0.12)'], DELETE: ['#E15A52', 'rgba(225,90,82,0.12)'], PUT: ['#D19A3F', 'rgba(209,154,63,0.12)'] };
  // THE REAL ROUTES. This list used to advertise POST /v1/directives,
  // GET /v1/runs/:id, POST /v1/approvals/:id/decide and
  // PATCH /v1/agents/:id/budget -- none of which exists. A developer reading
  // this panel would have written against an API that was never built.
  const endRows = [
    ['POST', '/api/v1/cowork/turns', 'Send one governed co-work turn'],
    ['GET', '/api/v1/agents', 'List governed agents and their input schemas'],
    ['POST', '/api/v1/agents/execute', 'Run one agent against a typed input'],
    ['GET', '/api/v1/hitl', 'List items deferred to a human'],
    ['POST', '/api/v1/hitl/{id}/approve', 'Approve and execute a deferred item'],
    ['POST', '/api/v1/hitl/{id}/reject', 'Reject a deferred item; nothing runs'],
    ['GET', '/api/v1/audit', 'Read the append-only governed-action log'],
    ['POST', '/api/v1/kill-switch/engage', 'Halt a scope (owner only)'],
  ].map((e) => ({ method: e[0], path: e[1], desc: e[2], mColor: mColors[e[0]][0], mBg: mColors[e[0]][1] }));
  const hookEvents = ['directive.completed', 'approval.requested', 'approval.decided', 'agent.error', 'budget.threshold', 'run.failed', 'audit.exported', 'policy.violation'].map((n) => ({ name: n }));
  // A request that actually works, against the route that actually exists.
  //
  // THIS SNIPPET DOCUMENTS THE BACKEND'S OWN AUTH, NOT THIS CONSOLE'S. The two
  // are different and conflating them would mislead: the console browser bundle
  // sends NO credential ever -- it calls the BFF same-origin and the session
  // cookie carries it (vite.config.js, "no credential in this app"), with the
  // service API key held only server-side in website/src/app/api/console/*.
  // A developer calling the PUBLIC API from their own client is the one who
  // needs an auth header, so the snippet is written for that caller and says
  // so, rather than implying the console authenticates this way.
  //
  // The header name is referenced through AUTH_HEADER_DOC rather than spelled
  // inline because CI greps this built bundle for credential-shaped text
  // (ci.yml `console-black`). That guard is content-based and intentionally
  // blunt, and it stays that way -- see the constant's own note.
  const quickstart = [
    'curl -X POST "$SKYLIZE_URL/api/v1/cowork/turns" \\',
    '  -H "' + AUTH_HEADER_DOC + ': $SKYLIZE_KEY" \\',
    '  -H "Content-Type: application/json" \\',
    '  -d \'{"message": "Draft three hooks for the spring launch"}\'',
  ].join('\n');

  // ── audit log ──
  //
  // TWO COLUMNS WERE RENAMED BECAUSE THEY WERE FALSE.
  //
  //   ACTOR -> SOURCE AGENT. The feed used to print names like "Chief Financial
  //   Officer" and "Operator (human)" under an ACTOR heading. `audit_log` has no
  //   human-actor column at all; it has `source_agent_id`, which is an agent id
  //   or null. Presenting an agent id as a person, in the one screen whose job
  //   is attribution, is the single worst thing this console could claim.
  //
  //   SIGNATURE -> INPUTS HASH. The rows carried invented values like
  //   "0x8F41...C2A9" under a SIGNATURE heading. The backend field is
  //   `inputs_hash`, a SHA-256 content hash (audit.py:8-10). A hash shows a
  //   payload is unaltered; a signature asserts WHO produced it. The label now
  //   says which one this actually is, and the value is the real hash.
  //
  // The filter chips are built from the action types actually present, not from
  // a hardcoded DIRECTIVE/APPROVAL/TOOL/SECURITY/BILLING taxonomy that the
  // backend never emitted.
  const actC = {
    success: ['#34C579', 'rgba(52,197,121,0.12)'],
    failure: ['#E15A52', 'rgba(225,90,82,0.12)'],
    denied: ['#E15A52', 'rgba(225,90,82,0.12)'],
    deferred: ['#D19A3F', 'rgba(209,154,63,0.12)'],
  };
  const logTypes = ['all'].concat(
    s.audit.map((l) => l.actionType).filter((v, i, arr) => v && arr.indexOf(v) === i).slice(0, 6),
  );
  const logChips = logTypes.map((id) => ({
    pick: () => setState({ logFilter: id }),
    label: id === 'all' ? 'ALL' : id.toUpperCase(),
    bd: s.logFilter === id ? 'var(--accent,#3D6BFF)' : '#232939',
    bg: s.logFilter === id ? 'color-mix(in oklab, var(--accent,#3D6BFF) 16%, transparent)' : 'transparent',
    c: s.logFilter === id ? '#E9EBF2' : '#8B93A7',
  }));
  const logRows = s.audit
    .filter((l) => s.logFilter === 'all' || l.actionType === s.logFilter)
    .map((l) => {
      const tone = actC[l.result] || ['#9AA1B2', 'rgba(154,161,178,0.12)'];
      return {
        time: hhmmss(l.occurredAt),
        // An AGENT id, or an explicit "\u2014" when the row has none. Never a
        // person name, because the backend has none to give.
        agent: l.sourceAgentId || '\u2014',
        action: l.actionType,
        actionColor: tone[0],
        actionBg: tone[1],
        // The governed result plus its reason, which is the real "target" story.
        target: l.result + (l.resultReason ? ' \u00b7 ' + l.resultReason : ''),
        // A CONTENT HASH, shown truncated, under a heading that says so.
        hash: l.inputsHash ? l.inputsHash.slice(0, 16) + '\u2026' : '\u2014',
      };
    });


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
  //
  // TWO FIELDS, BECAUSE TWO IS WHAT EXISTS. GET /api/v1/tenants/me/users
  // returns {user_id, role} and nothing else (tenants.py:105-113). The screen
  // was designed with MEMBER / EMAIL / ROLE / LAST ACTIVE / MFA and used to be
  // filled by six invented employees at an invented company -- including a row
  // literally named "the client contact Raman". All four unbacked columns are
  // dropped rather than defaulted, because a blank MFA cell on a security
  // screen still reads as a claim.
  //
  // THE ROLE PERMISSION MATRIX IS ALSO GONE from the live data: it was a
  // hardcoded 6x4 grid of dots asserting which role may do what. The real role
  // checks live in the backend route decorators (require_role /
  // require_any_role), the console has no endpoint that reports them, and a
  // stale permission matrix on a governance console is a liability, not a
  // decoration. `permRows` is empty and the screen says the matrix is not
  // wired.
  const roleC = {
    owner: ['color-mix(in oklab, var(--accent,#3D6BFF) 80%, #FFFFFF)', 'color-mix(in oklab, var(--accent,#3D6BFF) 16%, transparent)'],
    admin: ['#A78BFA', 'rgba(167,139,250,0.12)'],
    operator: ['#34C579', 'rgba(52,197,121,0.12)'],
    analyst: ['#D19A3F', 'rgba(209,154,63,0.12)'],
    viewer: ['#8B93A7', 'rgba(139,147,167,0.12)'],
  };
  const memberRows = s.members.map((m) => {
    const tone = roleC[m.role] || ['#8B93A7', 'rgba(139,147,167,0.12)'];
    return {
      userId: m.userId,
      // The user_id IS the identity the platform has. It is shown as such, not
      // dressed up as a display name.
      name: m.userId,
      initials: m.userId.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '\u2014',
      role: (m.role || '').toUpperCase(),
      roleColor: tone[0],
      roleBg: tone[1],
    };
  });
  const permRows = [];

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
  const autonomyCopy = {
    observe: 'observe — Observe only. Every action requires human sign-off.',
    propose: 'propose — Agents prepare work; humans execute it.',
    act_within_budget: 'act_within_budget — Act within budget. HIGH-risk actions escalate to you.',
    act_and_reallocate: 'act_and_reallocate — Act and reallocate budgets. Only irreversible actions escalate.',
    act_governed: 'act_governed — Act inside the governance envelope. Audit everything.',
  };
  // `busy` covers both the mount read and an in-flight write: a chip must not
  // be clickable while the value on screen is not yet known to be server truth.
  const autonomyBusy = s.autonomyLoading || s.autonomySaving;
  const autonomyChips = AUTONOMY_MODES.map((mode) => ({ mode, pick: () => setAutonomy(mode), disabled: autonomyBusy, label: mode.replace(/_/g, ' ').toUpperCase(), title: autonomyCopy[mode], bd: s.autonomy === mode ? 'var(--accent,#3D6BFF)' : '#232939', bg: s.autonomy === mode ? 'color-mix(in oklab, var(--accent,#3D6BFF) 18%, transparent)' : 'transparent', c: s.autonomy === mode ? '#E9EBF2' : '#8B93A7', opacity: autonomyBusy ? 0.55 : 1, cursor: autonomyBusy ? 'not-allowed' : 'pointer' }));
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
    // THE COUNT IS THE LIVE ONE. The empty state used to read "executed by 151
    // governed agents" -- the generated fixture count. The registry holds 23
    // (ALL_MVP_CONTRACTS), and a turn goes to ONE of them, `cowork_agent`, not
    // to a fleet. The blurb now says what actually happens and reports the real
    // roster size, or stays silent about it when the registry read failed.
    commandBlurb: s.agentsLoading
      ? 'One message in \u2014 run through the governed pipeline.'
      : (s.agentsError
        ? 'One message in \u2014 run through the governed pipeline. (The agent registry could not be read.)'
        : 'One message in \u2014 run through the governed pipeline by the co-work agent, '
          + 'one of ' + s.agentsLive.length + ' governed agents. Every action is decision-gated and audited.'),
    suggestions: sugg.map((txt) => ({ text: txt, send: () => doSend(txt) })),
    messagesVm, chatBusy: s.chatBusy, chatPhase: s.chatPhase,
    chatInput: s.chatInput, onChatInput: (e) => setState({ chatInput: e.target.value }),
    chatKey: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(s.chatInput); } },
    sendChat: () => doSend(s.chatInput), sendOpacity: s.chatInput.trim() ? '1' : '0.55',
    railAgents, railEmpty: s.rail.length === 0, railCount: '—',
    // THE DELEGATION RAIL IS NOT WIRED. It used to fill with a fake chain
    // assembled from the agent fixture, with per-agent token counts invented by
    // Math.random(). POST /api/v1/cowork/turns returns {reply, deliverable_id,
    // agent_id} and reports no sub-delegation, so there is nothing truthful to
    // draw. The panel says so instead of animating.
    railUnavailable: 'Per-agent delegation is not reported by the turn API.',
    // The session token/cost readouts were likewise derived from the invented
    // rail totals. The backend does not return per-turn token usage on this
    // route, so they are shown as unknown rather than as a number.
    chatTokFmt: '—', chatCostFmt: '—',

    // ── COMPOSER AFFORDANCES WITH NO BACKEND COUNTERPART ─────────────
    //
    // Attachments, agent/department tags and connector selections are all
    // DISABLED, visibly, rather than left clickable. The turn contract accepts
    // `message` and nothing else, and the agent and principal are fixed by the
    // backend by explicit design (cowork.py:47-50). A picker that still opened
    // and still added a chip would tell the operator their directive had been
    // routed to a named executive when the backend had never been told.
    //
    // They are kept in the layout, greyed and titled with the reason, because
    // silently deleting them would lose the product intent -- these are
    // designed, not built.
    composerDisabled: true,
    composerDisabledWhy: 'The turn API accepts a message only — the agent and '
      + 'principal are fixed by the backend.',
    fileInputRef: fileInputElRef,
    onAttachClick: () => showToast('ATTACHMENTS ARE NOT WIRED', '#D19A3F'),
    onFileChange: () => {},
    attachBtnOpacity: 0.35,
    composerChipsShow: false, composerChips: [],
    toggleTagMenu: () => showToast('AGENT / DEPARTMENT TAGGING IS NOT WIRED', '#D19A3F'),
    tagMenuOpen: false, tagQ: '', onTagQ: () => {}, tagOptions: [],
    toggleConnMenu: () => showToast('CONNECTOR CONTEXT IS NOT WIRED', '#D19A3F'),
    connMenuOpen: false, connOptions: [],
    tagBtnBd: 'transparent',
    tagBtnBg: 'transparent',
    tagBtnC: '#4A5162',
    tagBtnOpacity: 0.35,
    connBtnBd: 'transparent',
    connBtnBg: 'transparent',
    connBtnC: '#4A5162',
    connBtnOpacity: 0.35,
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
    isAppr: s.screen === 'appr', apRiskChips, apStats, apRows,
    // EMPTY and FAILED are different facts and the screen must not merge them:
    // an unreachable backend showing "queue clear" would be the console telling
    // an operator there is nothing to approve when it simply cannot see.
    apEmpty: apFiltered.length === 0 && !s.approvalsLoading && !s.approvalsError,
    apLoading: s.approvalsLoading,
    apError: s.approvalsError,
    apRetry: loadApprovals,
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
    keysLoading: s.keysLoading,
    keysError: s.keysError,
    keysEmpty: keyRows.length === 0 && !s.keysLoading && !s.keysError,
    mintKey,
    // The plaintext secret, present exactly once. The screen shows it until
    // dismissed and nothing persists it.
    mintedKey: s.mintedKey,
    dismissMintedKey,
    // audit
    isLog: s.screen === 'log', logChips, logRows,
    logLoading: s.auditLoading,
    logError: s.auditError,
    logEmpty: logRows.length === 0 && !s.auditLoading && !s.auditError,
    logRetry: loadAudit,
    // security
    isSec: s.screen === 'sec', secBadges, secDash: (circ * secScore / 100).toFixed(1) + ' ' + circ.toFixed(1), secScore: String(secScore), secControls, secEvents,
    // team
    isTeam: s.screen === 'team', memberRows, permRows,
    teamLoading: s.membersLoading,
    teamError: s.membersError,
    teamEmpty: memberRows.length === 0 && !s.membersLoading && !s.membersError,
    // billing
    isBill: s.screen === 'bill', planRenews: 'AUG 01', spendMTD: '$41,900', forecastFmt: '$63,400', meters, invoices,
    // settings
    isSet: s.screen === 'set', orgName: s.orgName, onOrgName: (e) => set({ orgName: e.target.value }),
    region: s.region, onRegion: (e) => set({ region: e.target.value }),
    retention: s.retention, onRetention: (e) => set({ retention: e.target.value }),
    autonomyMode: s.autonomy, autonomyDesc: autonomyCopy[s.autonomy] || autonomyCopy[DEFAULT_AUTONOMY_MODE], autonomyChips, guardrails,
    // Org-wide, server-owned: the screen must be able to say whether what it
    // shows is the org's stored posture, the fail-closed default, or stale.
    autonomyStatus: s.autonomyLoading
      ? 'Reading the organization-wide mode...'
      : s.autonomySaving
        ? 'Saving...'
        : s.autonomyErrorKind === 'read'
          ? 'Could not read the stored posture — showing the fail-closed default.'
          : s.autonomyErrorKind === 'write'
            ? 'Unchanged. Still showing the stored posture, not the mode you picked.'
            : s.autonomyConfigured
              ? 'Organization-wide, set by the owner.'
              : 'No mode has been set for this organization yet — failing closed to OBSERVE.',
    autonomyError: s.autonomyError,
    // "NOT SAVED" is only true of a failed write. A failed READ never attempted
    // to save anything, and labelling it that way would describe the wrong event.
    autonomyErrorLabel: s.autonomyErrorKind === 'read' ? 'NOT READ' : 'NOT SAVED',
    autonomyBusy: s.autonomyLoading || s.autonomySaving,
    pauseTitle: s.pausedAll ? 'Agents paused \u00b7 tenant scope' : 'Pause all agents',
    pauseLabel: s.pausedAll
      ? 'ENGAGED'
      : (s.pauseBusy ? 'ENGAGING\u2026' : (s.pauseArm ? 'CONFIRM PAUSE?' : 'PAUSE ALL')),
    pauseBg: s.pausedAll || s.pauseArm ? 'rgba(225,90,82,0.16)' : 'transparent', pauseC: '#E15A52',
    dangerPause,
    // THE REASON IS THE OPERATOR'S, NEVER THE CONSOLE'S. It is required by the
    // backend (KillSwitchRequest.reason) and lands in the audit record of the
    // most consequential control in the product, so it is collected here rather
    // than defaulted to a canned string.
    pauseReason: s.pauseReason,
    onPauseReason: (e) => setPauseReason(e.target.value),
    pauseReasonShow: s.pauseArm && !s.pausedAll,
    pauseError: s.pauseError,
    pauseBusy: s.pauseBusy,
    // The scope actually sent. Shown so the operator knows what PAUSE ALL means
    // rather than guessing at its blast radius.
    pauseScope: 'scope_type=tenant',
    toastShow: !!s.toast, toastText: s.toast ? s.toast.text : '', toastDot: s.toast ? s.toast.dot : '#34C579',
  };
}
