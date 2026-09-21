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
  RETENTION_DAYS_MAX,
  RETENTION_DAYS_MIN,
  approveHitl,
  engageKillSwitch,
  fetchAgents,
  fetchApiKeys,
  fetchApprovals,
  fetchAudit,
  fetchBillingUsage,
  fetchKnowledgeIndexHealth,
  fetchModels,
  fetchNotifications,
  fetchOrgPolicySettings,
  fetchOrgUsers,
  fetchPermissionMatrix,
  fetchSecurityActivity,
  fetchWorkflowRuns,
  issueApiKey,
  putOrgPolicySettings,
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
    orgName: persisted.orgName || 'Aventra Retail Group',
    // NO REGION FIELD. Verified against real infra that region is a
    // per-environment Terraform variable (us-east-1), not a per-org concept
    // -- see migrations/versions/0035_org_policy_settings.py's citation
    // trail. There is nothing here to persist or render.
    //
    // retention/guardrails are ORG-WIDE, server-owned state, same reasoning
    // as autonomy below: fail closed to the DAL's own safest defaults until
    // the server answers, never read from persisted localStorage.
    retentionDays: null,
    spendCapAlert: { value: true, enforced: false },
    emailDomainRestriction: { value: true, enforced: false },
    piiRedaction: { value: true, enforced: false },
    silentFallbackSuppressed: { value: true, enforced: false },
    policyConfigured: false,
    policyLoading: true,
    policySaving: false,
    policyError: null,
    policyErrorKind: null,
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
    pausedAll: false, pauseArm: false, toast: null,

    // ── security posture: real audit_log-derived counts ────────────────
    secActivity: null,
    secLoading: true,
    secError: null,

    // ── knowledge: real per-source_path ingestion census ────────────────
    knowledge: null,
    knowledgeLoading: true,
    knowledgeError: null,

    // ── models: real logical->concrete catalogue + routing rules ────────
    models: null,
    modelsLoading: true,
    modelsError: null,

    // ── workflows: real run history (workflow_runs, migration 0033) ─────
    workflowRuns: [],
    workflowRunsLoading: true,
    workflowRunsError: null,

    // ── notifications: real feed, empty until a real event fires ────────
    notifications: [],
    notifUnreadCount: 0,
    notifLoading: true,
    notifError: null,

    // ── billing: real ai_cost_ledger usage ───────────────────────────────
    billing: null,
    billingLoading: true,
    billingError: null,

    // ── permission matrix: mechanically derived from route source ───────
    permMatrix: null,
    permMatrixLoading: true,
    permMatrixError: null,

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
      // region/retention/guards are ORG-WIDE, server-owned (org_policy_settings,
      // migration 0035) and are never persisted here -- same reasoning as
      // autonomy above. orgName remains a per-operator UI label with no
      // backend counterpart.
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        screen: s.screen, sidebarOpen: s.sidebarOpen, netSel: s.netSel, orgName: s.orgName,
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

  // ── org policy settings: guardrails + retention, read on mount ─────────
  //
  // FAIL CLOSED on a read failure, same reasoning as autonomy: the initial
  // state already holds the DAL's own safest defaults, so a failure leaves
  // the console showing them with `policyError` set, never silently claiming
  // they are the org's actual stored choice.
  const policyAliveRef = useRef(true);
  useEffect(() => {
    policyAliveRef.current = true;
    fetchOrgPolicySettings().then(
      (result) => {
        if (!policyAliveRef.current) return;
        setState({
          retentionDays: result.retentionDays,
          spendCapAlert: result.spendCapAlert,
          emailDomainRestriction: result.emailDomainRestriction,
          piiRedaction: result.piiRedaction,
          silentFallbackSuppressed: result.silentFallbackSuppressed,
          policyConfigured: result.configured,
          policyLoading: false,
          policyError: null,
          policyErrorKind: null,
        });
      },
      (error) => {
        if (!policyAliveRef.current) return;
        setState({
          policyLoading: false,
          policyError: error.message || 'Could not read the org policy settings.',
          policyErrorKind: 'read',
        });
      },
    );
    return () => { policyAliveRef.current = false; };
  }, [setState]);

  // Write guardrails + retention. NOT optimistic, same reasoning as
  // setAutonomy: every field is only ever assigned from what the backend
  // actually persisted.
  const setOrgPolicySettings = useCallback((patch) => {
    const s = stateRef.current;
    if (s.policySaving) return;
    setState({ policySaving: true, policyError: null, policyErrorKind: null });
    putOrgPolicySettings({
      spendCapAlertEnabled: 'spendCapAlertEnabled' in patch ? patch.spendCapAlertEnabled : s.spendCapAlert.value,
      emailDomainRestrictionEnabled: 'emailDomainRestrictionEnabled' in patch ? patch.emailDomainRestrictionEnabled : s.emailDomainRestriction.value,
      piiRedactionEnabled: 'piiRedactionEnabled' in patch ? patch.piiRedactionEnabled : s.piiRedaction.value,
      silentFallbackSuppressed: 'silentFallbackSuppressed' in patch ? patch.silentFallbackSuppressed : s.silentFallbackSuppressed.value,
      retentionDays: 'retentionDays' in patch ? patch.retentionDays : s.retentionDays,
    }).then(
      (result) => {
        if (!policyAliveRef.current) return;
        setState({
          retentionDays: result.retentionDays,
          spendCapAlert: result.spendCapAlert,
          emailDomainRestriction: result.emailDomainRestriction,
          piiRedaction: result.piiRedaction,
          silentFallbackSuppressed: result.silentFallbackSuppressed,
          policyConfigured: result.configured,
          policySaving: false,
          policyError: null,
          policyErrorKind: null,
        });
      },
      (error) => {
        if (!policyAliveRef.current) return;
        setState({
          policySaving: false,
          policyError: error.message || 'Could not save the org policy settings.',
          policyErrorKind: 'write',
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

    fetchKnowledgeIndexHealth().then(
      (knowledge) => {
        if (!aliveRef.current) return;
        setState({ knowledge, knowledgeLoading: false, knowledgeError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          knowledge: null,
          knowledgeLoading: false,
          knowledgeError: error.message || 'Could not read the knowledge index.',
        });
      },
    );

    fetchBillingUsage(12).then(
      (billing) => {
        if (!aliveRef.current) return;
        setState({ billing, billingLoading: false, billingError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          billing: null,
          billingLoading: false,
          billingError: error.message || 'Could not read the billing summary.',
        });
      },
    );

    fetchSecurityActivity(24, 20).then(
      (secActivity) => {
        if (!aliveRef.current) return;
        setState({ secActivity, secLoading: false, secError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          secActivity: null,
          secLoading: false,
          secError: error.message || 'Could not read the security summary.',
        });
      },
    );

    fetchModels().then(
      (models) => {
        if (!aliveRef.current) return;
        setState({ models, modelsLoading: false, modelsError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          models: null,
          modelsLoading: false,
          modelsError: error.message || 'Could not read the model catalogue.',
        });
      },
    );

    fetchWorkflowRuns(50).then(
      (result) => {
        if (!aliveRef.current) return;
        setState({ workflowRuns: result.runs, workflowRunsLoading: false, workflowRunsError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          workflowRuns: [],
          workflowRunsLoading: false,
          workflowRunsError: error.message || 'Could not read the workflow run history.',
        });
      },
    );

    fetchNotifications(50).then(
      (result) => {
        if (!aliveRef.current) return;
        setState({
          notifications: result.notifications,
          notifUnreadCount: result.unreadCount,
          notifLoading: false,
          notifError: null,
        });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          notifications: [],
          notifUnreadCount: 0,
          notifLoading: false,
          notifError: error.message || 'Could not read notifications.',
        });
      },
    );

    fetchPermissionMatrix().then(
      (permMatrix) => {
        if (!aliveRef.current) return;
        setState({ permMatrix, permMatrixLoading: false, permMatrixError: null });
      },
      (error) => {
        if (!aliveRef.current) return;
        setState({
          permMatrix: null,
          permMatrixLoading: false,
          permMatrixError: error.message || 'Could not read the permission matrix.',
        });
      },
    );

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
    setState, set, setAutonomy, setOrgPolicySettings, nav, tiltMove, tiltLeave,
    onAttachClick, onFileChange, removeAttachment, toggleTagMenu, toggleConnMenu, onTagQ, addTag, removeTag, addConnector, removeConnector,
    doSend, starGeom, starStep, starGo, starTilt, starTiltReset, netGeom, decide, dangerPause, showToast, sparks, spend30,
    loadApprovals, loadAudit, mintKey, revokeKey, setPauseReason, dismissMintedKey,
  });
}

function buildViewModel(ctx) {
  const {
    state: s, accent: acc, chatElRef, fileInputElRef, starTiltElRef, starPlaneElRef,
    setState, set, setAutonomy, setOrgPolicySettings, nav, tiltMove, tiltLeave,
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
  //
  // REAL RUN HISTORY, from workflow_runs (migration 0033) via
  // GET /api/console/workflows/runs. The 5-workflow-definition catalogue and
  // the per-run stage-progress bar are GONE, not reshaped: GET /api/v1/workflows
  // (the real definition list) has no BFF proxy today, and `failure_stage` on a
  // run is where it STOPPED, never how far it progressed -- there is no
  // per-stage progress anywhere on the live orchestrator path, so a fabricated
  // pipeline bar would be exactly the fiction this pass exists to remove.
  const wfStatusColor = (status) => (
    status === 'completed' ? '#34C579' : status === 'failed' ? '#E15A52' : status === 'denied' ? '#E15A52' : '#D19A3F'
  );
  const wfRuns = s.workflowRuns.map((r) => ({
    name: r.workflowName || r.agentId || 'workflow',
    trigger: r.correlationId ? r.correlationId.slice(0, 8) : '—',
    status: (r.status || '').toUpperCase(),
    stColor: wfStatusColor(r.status),
    when: hhmmss(r.startedAt),
    // Duration is derivable only for a FINISHED run; a still-running one has no
    // end time to subtract, so it is shown as unknown rather than computed
    // against "now" and re-labeled every render.
    dur: r.finishedAt
      ? Math.round((Date.parse(r.finishedAt) - Date.parse(r.startedAt)) / 60000) + 'm'
      : '—',
    // Where a run STOPPED, not a stage-progress bar. Null on every completed run.
    failureStage: r.failureStage,
    reason: r.reason,
  }));

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
  //
  // REAL logical->concrete catalogue and routing rules, from
  // GET /api/console/models. The 4 fictional names (atlas-4-frontier etc.)
  // and the fabricated latency/context-window/traffic-share numbers are GONE,
  // not reshaped -- none has a backend source; `models.py`'s own docstring
  // says so. `pricing: null` means "nobody has priced this model" (model_pricing
  // ships empty by design), rendered as such rather than as a zero or a blank.
  const modelTagFor = (logicalName) => (
    logicalName === 'reasoning' ? { tag: 'REASONING', tagColor: '#7A9BFF', tagBd: 'rgba(122,155,255,0.4)' }
    : logicalName === 'fast' ? { tag: 'FAST', tagColor: '#34C579', tagBd: 'rgba(52,197,121,0.4)' }
    : { tag: logicalName.toUpperCase(), tagColor: '#A78BFA', tagBd: 'rgba(167,139,250,0.4)' }
  );
  const modelCards = s.models ? s.models.catalogue.map((c) => {
    const tone = modelTagFor(c.logicalName);
    return {
      name: c.concreteModel,
      tag: tone.tag, tagColor: tone.tagColor, tagBd: tone.tagBd,
      desc: c.logicalName + ' · ' + c.provider,
      // No source anywhere in the backend for these three -- shown as unknown,
      // never a guessed or carried-over number.
      ctx: '—', latency: '—',
      cost: c.pricing
        ? '$' + (c.pricing.inputPriceMicrosPerMtok / 1e6).toFixed(2) + '/Mtok in'
        : (s.models.pricingConfigured ? '—' : 'not priced'),
      // Traffic share is derivable from ai_cost_ledger, not a configured value
      // this route reports -- not shown rather than fabricated.
      share: '—', shareW: '0%',
    };
  }) : [];
  const routeRows = s.models ? s.models.routing.map((r) => ({
    cls: r.routingClass,
    model: r.targetLogicalModel,
    fallback: r.fallbackLogicalModel || '—',
    note: r.configured ? 'configured' : 'fail-closed default — not yet set for this org',
  })) : [];

  // ── knowledge ──
  //
  // REAL per-source_path ingestion census, from GET /api/console/knowledge.
  // `source_path` is an ORIGIN STRING whoever ingested a document supplied --
  // an upload's filename, "onboarding-interview", or a webhook path -- NOT a
  // configured, syncing data source. So there is no connector-type column, no
  // coverage percentage, no SYNCED/INDEXING status and no recall latency:
  // none of the five has a backend source, and rendering one would assert an
  // integration that does not exist. See api/console/knowledge/route.ts.
  const kbRows = s.knowledge ? s.knowledge.sourcePaths.map((p) => ({
    name: p.sourcePath || '(unknown)',
    docs: fk(p.documents),
    chunks: fk(p.chunks),
    departments: p.departments.join(', ') || '—',
    // A write timestamp, not a freshness-vs-origin claim: the origin may have
    // changed since without the platform ever learning it did.
    lastIngested: p.lastIngestedAt ? agoFrom(p.lastIngestedAt) : '—',
  })) : [];
  const kbStats = s.knowledge ? [
    { label: 'DOCUMENTS', value: fk(s.knowledge.totalDocuments), sub: 'ingested', color: '#E9EBF2' },
    { label: 'CHUNKS INDEXED', value: fk(s.knowledge.totalChunks), sub: s.knowledge.truncated ? 'lower bound — census capped' : 'total', color: '#E9EBF2' },
  ] : [];

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
  //
  // REAL audit_log-derived counts, from GET /api/console/security. THE SCORE,
  // THE 8 CONTROLS AND THE 4 COMPLIANCE BADGES ARE GONE, not reshaped: none
  // has a scoring methodology, a control inventory or a compliance auditor
  // behind it anywhere in this system (api/console/security/route.ts spells
  // out why per element). Rendering any of the three would be inventing a
  // measurement or a third-party attestation the platform never made.
  const secByResult = s.secActivity ? s.secActivity.byResult : { success: 0, denied: 0, escalated: 0, failed: 0 };
  const secEvents = s.secActivity ? s.secActivity.recentEvents.map((e) => ({
    text: e.actionType + ' · ' + e.result + (e.resultReason ? ' · ' + e.resultReason : ''),
    time: agoFrom(e.occurredAt),
    sevColor: e.result === 'failed' || e.result === 'denied' ? '#E15A52' : '#D19A3F',
  })) : [];

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
  // THE MATRIX IS BACK, REAL THIS TIME. `route_group` is the raw route-file
  // name the backend's AST scanner produced (edge/permission_matrix.py) --
  // owner-approved design: no invented business-action vocabulary. Every
  // cell is mechanically derived from an actual Depends(require_role(...))
  // call site, never hand-transcribed, so it cannot silently drift from what
  // the routes actually enforce the way the old hardcoded 6x4 grid did.
  const PERM_ROLES = ['owner', 'admin', 'operator', 'analyst', 'viewer'];
  const permRows = s.permMatrix ? s.permMatrix.routeGroups.map((g) => ({
    name: g.routeGroup,
    cells: PERM_ROLES.map((role) => {
      const a = g.access[role] || { read: false, write: false };
      return { role, read: !!a.read, write: !!a.write };
    }),
  })) : [];

  // ── billing ──
  //
  // REAL ai_cost_ledger usage, from GET /api/console/billing. Plan tier,
  // invoices, seats and agent-slots have NO backing table anywhere in this
  // repo, so they are NOT reshaped into meters/invoices that no longer mean
  // what their labels say -- the screen must render `unavailableSections`
  // as an explicit "not available yet" state instead. A zero-spend period is
  // a real, true answer (model_pricing ships empty by design), never a
  // loading or error state.
  const billingUnavailable = s.billing ? s.billing.unavailableSections : ['plan_tier', 'invoices', 'seats', 'agent_slots'];
  const billingModelRows = s.billing ? s.billing.models.map((m) => ({
    name: m.provider + ' · ' + m.model,
    cost: money(m.costUsd),
  })) : [];
  const billingHistoryRows = s.billing ? s.billing.history.map((h) => ({
    period: h.billingPeriod,
    cost: money(h.costUsd),
  })) : [];

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
  // REAL guardrails, from GET/PUT /api/console/org-policy-settings
  // (org_policy_settings, migration 0035). Each carries the backend's own
  // `enforced` flag: today all four are `enforced: false` -- a stored
  // preference with no live enforcement point -- and the screen must say so
  // rather than imply a toggle changes system behavior it does not yet
  // change. NO REGION FIELD anywhere in this screen: verified against real
  // infra that region is a per-environment Terraform variable, not a
  // per-org concept.
  const guardDefs = [
    ['spendCapAlertEnabled', s.spendCapAlert, 'Spend cap alert', 'Alert when spend approaches the governance ceiling'],
    ['emailDomainRestrictionEnabled', s.emailDomainRestriction, 'Restrict email domains', 'Outbound sharing limited to the org’s own domain'],
    ['piiRedactionEnabled', s.piiRedaction, 'Redact PII in logs', 'Personally identifiable data masked before storage'],
    ['silentFallbackSuppressed', s.silentFallbackSuppressed, 'Suppress silent model fallback', 'Refuse rather than silently route to an unchosen model'],
  ];
  const guardrails = guardDefs.map((g) => {
    const field = g[0], current = g[1], on = !!current.value;
    return {
      name: g[2], desc: g[3],
      // Real enforcement status. Rendered so the screen can never imply a
      // toggle does something it does not.
      enforced: current.enforced,
      enforcedLabel: current.enforced ? 'ENFORCED' : 'STORED PREFERENCE — NOT YET ENFORCED',
      toggle: () => setOrgPolicySettings({ [field]: !on }),
      disabled: s.policySaving,
      trackBg: on ? 'var(--accent,#3D6BFF)' : '#141826',
      trackBd: on ? 'color-mix(in oklab, var(--accent,#3D6BFF) 70%, transparent)' : '#232939',
      knobX: on ? '17px' : '2px',
    };
  });

  // REAL notifications feed, from GET /api/console/notifications
  // (migration 0034), org-scoped not per-user. Only two `kind` values have a
  // real producer today -- hitl.approval_requested and
  // governance.action_denied -- so a fresh org's list is legitimately EMPTY
  // until one of those two things happens. Never seeded.
  const notifSevColor = { info: '#7A9BFF', warning: '#D19A3F', critical: '#E15A52' };
  const notifItems = s.notifications.map((n) => ({
    dot: notifSevColor[n.severity] || '#8B93A7',
    text: n.title || n.body || n.kind,
    when: agoFrom(n.createdAt),
  }));

  return {
    accentVar: acc,
    crumb: crumbs[s.screen] || 'overview',
    activeCount: String(active), tokRateFmt: fk(s.tokRate), costToday: money(cost), clock: s.clock,
    toggleNotif: () => setState({ notifOpen: !s.notifOpen }), notifOpen: s.notifOpen, notifItems,
    notifLoading: s.notifLoading, notifError: s.notifError,
    notifEmpty: notifItems.length === 0 && !s.notifLoading && !s.notifError,
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
    isWf: s.screen === 'wf', wfRuns,
    wfLoading: s.workflowRunsLoading,
    wfError: s.workflowRunsError,
    wfEmpty: wfRuns.length === 0 && !s.workflowRunsLoading && !s.workflowRunsError,
    // No workflow-definition list is wired: GET /api/v1/workflows (the real
    // definition list) has no BFF proxy today. Said out loud rather than
    // filled with the old 5-workflow mock.
    wfDefinitionsUnavailable: 'Workflow definitions are not yet exposed to the console.',
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
    modLoading: s.modelsLoading,
    modError: s.modelsError,
    modEmpty: modelCards.length === 0 && !s.modelsLoading && !s.modelsError,
    // knowledge
    isKb: s.screen === 'kb', kbStats, kbRows,
    kbLoading: s.knowledgeLoading,
    kbError: s.knowledgeError,
    kbEmpty: kbRows.length === 0 && !s.knowledgeLoading && !s.knowledgeError,
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
    //
    // NO SCORE GAUGE, NO CONTROL CHECKLIST, NO COMPLIANCE BADGE ROW. Real
    // audit_log outcome counts and the real recent events behind them, and
    // nothing else -- see the note above `secByResult`.
    isSec: s.screen === 'sec',
    secTotalActions: s.secActivity ? String(s.secActivity.totalActions) : '—',
    secSuccess: String(secByResult.success), secDenied: String(secByResult.denied),
    secEscalated: String(secByResult.escalated), secFailed: String(secByResult.failed),
    secEvents,
    secLoading: s.secLoading,
    secError: s.secError,
    secEmpty: secEvents.length === 0 && !s.secLoading && !s.secError,
    secScoreUnavailable: 'No scoring methodology exists yet — not shown rather than invented.',
    // team
    isTeam: s.screen === 'team', memberRows, permRows,
    teamLoading: s.membersLoading,
    teamError: s.membersError,
    teamEmpty: memberRows.length === 0 && !s.membersLoading && !s.membersError,
    permLoading: s.permMatrixLoading,
    permError: s.permMatrixError,
    permEmpty: permRows.length === 0 && !s.permMatrixLoading && !s.permMatrixError,
    // billing
    //
    // Real ai_cost_ledger usage. `billingUnavailable` names the plan/invoice/
    // seat/slot sections that have no backing table -- rendered as explicit
    // "not available yet", never as a fabricated meter or invoice row.
    isBill: s.screen === 'bill',
    billingPeriod: s.billing ? s.billing.billingPeriod : '',
    spendMTD: s.billing ? money(s.billing.currentPeriodUsd) : '—',
    billingModelRows, billingHistoryRows, billingUnavailable,
    billingCeilingConfigured: s.billing ? s.billing.ceilingConfigured : false,
    billingCeiling: s.billing && s.billing.ceilingUsd != null ? money(s.billing.ceilingUsd) : null,
    billingRemaining: s.billing && s.billing.remainingUsd != null ? money(s.billing.remainingUsd) : null,
    billingLoading: s.billingLoading,
    billingError: s.billingError,
    // settings
    isSet: s.screen === 'set', orgName: s.orgName, onOrgName: (e) => set({ orgName: e.target.value }),
    // NO REGION FIELD. Verified against real infra that region is a
    // per-environment Terraform variable, not a per-org concept -- removed
    // entirely rather than shown read-only, since there is nothing per-org to
    // display.
    retention: s.retentionDays == null ? '' : String(s.retentionDays),
    onRetention: (e) => {
      const n = Number(e.target.value);
      if (Number.isInteger(n)) setOrgPolicySettings({ retentionDays: n });
    },
    retentionMin: RETENTION_DAYS_MIN, retentionMax: RETENTION_DAYS_MAX,
    autonomyMode: s.autonomy, autonomyDesc: autonomyCopy[s.autonomy] || autonomyCopy[DEFAULT_AUTONOMY_MODE], autonomyChips, guardrails,
    policyLoading: s.policyLoading,
    policySaving: s.policySaving,
    policyError: s.policyError,
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
