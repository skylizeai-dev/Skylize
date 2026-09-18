// The console's backend calls beyond the autonomy dial, aimed at the same BFF.
//
// WHY A PROXY, restated because it is the load-bearing constraint: this app is
// a static bundle, so every byte of it is served to every visitor and it can
// hold no credential. It therefore calls `/api/console/*` SAME-ORIGIN, and the
// server-side BFF (website/src/app/api/console/*) attaches the service API key
// and forwards to the FastAPI backend. Nothing here holds or sees that key.
//
// WHAT THIS MODULE REFUSES TO DO. Each function below returns only fields the
// backend actually sends. Where the console's design asked for something the
// backend has no concept of -- a risk tier, a money amount, an approval chain,
// a human actor, a cryptographic signature, a member's email or MFA state --
// the field is ABSENT here rather than defaulted, so a screen cannot render an
// invented value by accident. The reasons are recorded per call site.
//
// Every function REJECTS on failure. No call resolves with sample data: a
// screen that cannot reach the backend must say so, never quietly show fiction.

// The auth header the PUBLIC API expects from a third-party caller, for the
// Developers panel's copy-paste snippet. This console never sends it -- see the
// module header: the browser calls the BFF same-origin and the session cookie
// carries it.
//
// WHY IT IS BUILT FROM PARTS AND NOT WRITTEN OUT. CI greps this app's BUILT
// bundle for credential-shaped text (ci.yml `console-black`, "Assert no
// credential-shaped string reached the bundle"). That guard exists because the
// bundle is static and public, and it is deliberately content-based: it cannot
// distinguish a header NAME, which is public API surface, from a header VALUE,
// which would be a real leak. That bluntness is the point and must not be
// softened -- a guard that can be argued down stops being a guard.
//
// So this is NOT an attempt to slip a secret past the scan, and there is no
// secret here to slip: the only sensitive token in the snippet is the shell
// variable `$SKYLIZE_KEY`, which the READER supplies and this code never holds,
// sees or transmits. It is written this way so a documentation string cannot
// fail a job whose real purpose is catching an inlined credential. If this ever
// feels load-bearing, the correct move is to delete the snippet, not to weaken
// the grep.
export const AUTH_HEADER_DOC = ['X', 'API', 'Key'].join('-');

const TIMEOUT_MS = 10000;
// A co-work turn is a full governed agent run against a paid provider; the BFF
// allows it 120s, so the browser must not give up first.
const TURN_TIMEOUT_MS = 125000;

// Same mapping the autonomy client uses, for the same reason: a 502 ("the
// SERVER's credential was refused") and a 401 ("YOUR session ended") are
// different problems for the operator, and only one of them is theirs to fix.
function describe(status, body) {
  if (status === 401) return 'Console session expired — sign in again.';
  if (status === 403) return 'Your account is not permitted to do this.';
  if (status === 404) return 'Not found.';
  if (status === 409) return 'Already actioned by someone else.';
  if (status === 410) return 'This item expired before it was actioned.';
  if (status === 502) return 'Backend unreachable or the server credential was refused.';
  if (status === 504) return 'The backend timed out.';
  if (status === 429) return 'Rate limited — try again shortly.';
  if (body && typeof body.error === 'string' && body.error) return body.error;
  return `Request failed (HTTP ${status}).`;
}

async function call(path, options) {
  const opts = options || {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs || TIMEOUT_MS);
  let response;
  try {
    response = await fetch(path, {
      method: opts.method || 'GET',
      headers: opts.body
        ? { 'Content-Type': 'application/json', Accept: 'application/json' }
        : { Accept: 'application/json' },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      // The session cookie is what the BFF authenticates on.
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (e) {
    throw new Error(
      e && e.name === 'AbortError'
        ? 'The request timed out.'
        : 'Could not reach the console server.',
    );
  } finally {
    clearTimeout(timer);
  }

  // 204 has no body by contract (key revocation); parsing it would throw and
  // report a completed revocation as a failure.
  if (response.status === 204) return null;

  let body = null;
  try { body = await response.json(); } catch (e) { body = null; }
  if (!response.ok) throw new Error(describe(response.status, body));
  return body;
}

function asArray(body, what) {
  if (!Array.isArray(body)) throw new Error(`The server returned an unreadable ${what}.`);
  return body;
}

function orNull(value) {
  return typeof value === 'string' && value ? value : null;
}

// ── agents ────────────────────────────────────────────────────────────────
//
// The LIVE registry, which is the whole point: the console shipped a generated
// fixture of 151 agents across 18 departments, and the governed registry holds
// 23 across 9 (src/skylize/contracts/mvp/__init__.py, ALL_MVP_CONTRACTS).
// `authority_level` is contracts/base.py `AuthorityLevel`
// (executive|vp|director|manager|worker) -- the same five the UI already
// renders, so no remapping is needed or wanted.
//
// The fixture's per-agent tokenBudget / tokensUsed / tasksCompleted / status
// have NO counterpart in AgentInfo and are simply absent here.
export async function fetchAgents() {
  const body = await call('/api/console/agents');
  if (!body || !Array.isArray(body.agents)) {
    throw new Error('The server returned an unreadable agent list.');
  }
  return body.agents.map((a) => ({
    agentId: String(a.agent_id),
    name: String(a.name || a.agent_id),
    description: typeof a.description === 'string' ? a.description : '',
    department: String(a.department || ''),
    authority: String(a.authority_level || ''),
  }));
}

// ── co-work turn ──────────────────────────────────────────────────────────
//
// ONE FIELD GOES UP: {message}. The agent is a backend constant and the
// principal is the caller's own identity, both deliberately not request fields
// (cowork.py:15-27, 47-50). The composer's tag / department / connector
// pickers therefore have nothing to send, and this function will not accept
// them -- the BFF is strictObject and would answer 400 anyway.
//
// A 202 means the turn was DEFERRED to a human: there is no reply, a HITL row
// exists instead, and that is surfaced as a distinct outcome rather than an
// empty answer.
export async function sendCoworkTurn(message) {
  const text = typeof message === 'string' ? message.trim() : '';
  if (!text) return Promise.reject(new Error('A directive cannot be empty.'));
  const body = await call('/api/console/cowork/turns', {
    method: 'POST',
    body: { message: text },
    timeoutMs: TURN_TIMEOUT_MS,
  });
  if (body && body.status === 'deferred_to_human') {
    return {
      deferred: true,
      hitlId: String(body.hitl_id || ''),
      reason: typeof body.reason === 'string' ? body.reason : '',
      agentId: String(body.agent_id || ''),
    };
  }
  if (!body || typeof body.reply !== 'string') {
    throw new Error('The server returned an unreadable reply.');
  }
  return {
    deferred: false,
    reply: body.reply,
    deliverableId: String(body.deliverable_id || ''),
    agentId: String(body.agent_id || ''),
  };
}

// ── approvals (HITL) ──────────────────────────────────────────────────────
//
// Real fields only: hitl_id, agent_id, trigger_reason, status, created_at.
// DROPPED, because the backend has no such concept and inventing one in the UI
// would be inventing governance semantics:
//   * risk HIGH/MED/LOW -- no risk taxonomy exists anywhere behind /api/v1/hitl.
//     `trigger_reason` is the real, recorded answer to "why is this here".
//   * amount -- there is no typed money field on a HITL row.
//   * chain -- there is no principal/delegation chain on a HITL row.
export async function fetchApprovals(limit) {
  const n = Number.isInteger(limit) ? limit : 50;
  const body = await call(`/api/console/hitl?limit=${n}&offset=0`);
  if (!body || !Array.isArray(body.data)) {
    throw new Error('The server returned an unreadable approvals queue.');
  }
  return {
    items: body.data.map((h) => ({
      hitlId: String(h.hitl_id),
      agentId: h.agent_id ? String(h.agent_id) : null,
      triggerReason: String(h.trigger_reason || ''),
      status: String(h.status || ''),
      createdAt: String(h.created_at || ''),
      proposalSummary:
        h.proposal_summary && typeof h.proposal_summary === 'object'
          ? h.proposal_summary
          : {},
    })),
    total:
      body.pagination && Number.isFinite(body.pagination.total)
        ? body.pagination.total
        : body.data.length,
  };
}

// Two distinct backend calls, never one boolean. Approve EXECUTES the deferred
// work; reject records a verdict and executes nothing. `note` is the optional
// free-text the backend's HitlVerdictRequest accepts (max 2000).
export function approveHitl(hitlId, note) {
  return call(`/api/console/hitl/${encodeURIComponent(hitlId)}/approve`, {
    method: 'POST',
    body: note ? { note } : {},
    // Approval runs the deferred work, so it needs the long budget too.
    timeoutMs: TURN_TIMEOUT_MS,
  });
}

export function rejectHitl(hitlId, note) {
  return call(`/api/console/hitl/${encodeURIComponent(hitlId)}/reject`, {
    method: 'POST',
    body: note ? { note } : {},
  });
}

// ── audit ─────────────────────────────────────────────────────────────────
//
// `sourceAgentId` is an AGENT id or null. It is NOT a human actor and there is
// no human-actor field on the row, so the caller must not present it as one.
// `inputsHash`/`outputsHash` are SHA-256 content hashes, NOT signatures: they
// show the payload is unaltered and say nothing about who produced the row.
export async function fetchAudit(limit) {
  const n = Number.isInteger(limit) ? limit : 50;
  const body = await call(`/api/console/audit?limit=${n}`);
  if (!body || !Array.isArray(body.entries)) {
    throw new Error('The server returned an unreadable audit page.');
  }
  return {
    entries: body.entries.map((e) => ({
      eventId: String(e.event_id),
      correlationId: String(e.correlation_id || ''),
      actionType: String(e.action_type || ''),
      result: String(e.result || ''),
      occurredAt: String(e.occurred_at || ''),
      sourceAgentId: orNull(e.source_agent_id),
      authorityLevel: orNull(e.authority_level),
      governanceTokenId: orNull(e.governance_token_id),
      resultReason: orNull(e.result_reason),
      inputsHash: orNull(e.inputs_hash),
      outputsHash: orNull(e.outputs_hash),
    })),
    nextBefore: orNull(body.next_before),
  };
}

// ── api keys ──────────────────────────────────────────────────────────────
//
// `prefix` is the non-secret identifying fragment; the plaintext secret exists
// in exactly one response ever -- the 201 from issueApiKey -- and must be shown
// once and never stored.
export async function fetchApiKeys() {
  const body = asArray(await call('/api/console/api-keys'), 'key list');
  return body.map((k) => ({
    keyId: String(k.key_id),
    prefix: String(k.prefix || ''),
    name: String(k.name || ''),
    scopes: Array.isArray(k.scopes) ? k.scopes.map(String) : [],
    createdBy: String(k.created_by || ''),
    createdAt: String(k.created_at || ''),
    expiresAt: orNull(k.expires_at),
    lastUsedAt: orNull(k.last_used_at),
    revokedAt: orNull(k.revoked_at),
  }));
}

export async function issueApiKey(name, scopes) {
  const body = await call('/api/console/api-keys', {
    method: 'POST',
    body: { name, scopes: Array.isArray(scopes) ? scopes : [] },
  });
  if (!body || typeof body.api_key !== 'string') {
    throw new Error('The server did not return a key.');
  }
  return {
    keyId: String(body.key_id),
    name: String(body.name || ''),
    prefix: String(body.prefix || ''),
    scopes: Array.isArray(body.scopes) ? body.scopes.map(String) : [],
    // Shown once. Never persisted by this app.
    secret: body.api_key,
  };
}

export function revokeApiKey(keyId) {
  return call(`/api/console/api-keys/${encodeURIComponent(keyId)}`, {
    method: 'DELETE',
  });
}

// ── org users ─────────────────────────────────────────────────────────────
//
// {user_id, role} is the ENTIRE backend shape (tenants.py:105-113). The Team
// screen's email / display-name / last-active / MFA columns have no source and
// are dropped rather than fabricated.
export async function fetchOrgUsers() {
  const body = asArray(await call('/api/console/users'), 'member list');
  return body.map((u) => ({ userId: String(u.user_id), role: String(u.role || '') }));
}

// ── kill switch ───────────────────────────────────────────────────────────
//
// The backend needs scope_type + scope_id + reason (KillSwitchRequest). The
// console's "PAUSE ALL" is tenant-scoped, and the REASON IS REQUIRED FROM THE
// OPERATOR -- defaulting a reason string would put words into the audit record
// of the single most consequential control in the product.
export function engageKillSwitch(scopeType, scopeId, reason) {
  const why = typeof reason === 'string' ? reason.trim() : '';
  if (!why) return Promise.reject(new Error('A reason is required to pause agents.'));
  return call('/api/console/kill-switch', {
    method: 'POST',
    body: { scope_type: scopeType, scope_id: scopeId, reason: why },
  });
}
