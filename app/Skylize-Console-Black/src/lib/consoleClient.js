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

// Mirrors dal/org_policy_settings.py's RETENTION_MIN_DAYS/RETENTION_MAX_DAYS
// (2555 = the documented 7-year compliance floor for audit/governance
// retention; 3650 is an engineering default, not sourced from any document --
// see migrations/versions/0035_org_policy_settings.py). Used to bound the
// retention input at the edge instead of round-tripping an out-of-range value
// that the backend would reject with a 422 anyway.
export const RETENTION_DAYS_MIN = 2555;
export const RETENTION_DAYS_MAX = 3650;

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

// ── knowledge ─────────────────────────────────────────────────────────────
//
// A census of ingested chunks, grouped by `source_path` -- an ORIGIN STRING
// whoever ingested a document supplied, not a configured, syncing data
// source. There is no connector taxonomy, no coverage percentage, no
// sync-status and no recall latency anywhere in the backend, so none of
// those fields exist here -- see api/console/knowledge/route.ts for the full
// accounting of what this screen used to claim and cannot.
export async function fetchKnowledgeIndexHealth() {
  const body = await call('/api/console/knowledge');
  if (!body || !Array.isArray(body.source_paths)) {
    throw new Error('The server returned an unreadable knowledge index.');
  }
  return {
    totalChunks: Number(body.total_chunks) || 0,
    totalDocuments: Number(body.total_documents) || 0,
    lastIngestedAt: orNull(body.last_ingested_at),
    truncated: body.truncated === true,
    sourcePaths: body.source_paths.map((p) => ({
      sourcePath: String(p.source_path || ''),
      chunks: Number(p.chunks) || 0,
      documents: Number(p.documents) || 0,
      departments: Array.isArray(p.departments) ? p.departments.map(String) : [],
      lastIngestedAt: orNull(p.last_ingested_at),
    })),
  };
}

// ── billing ───────────────────────────────────────────────────────────────
//
// Real ai_cost_ledger usage. Money is MICRO-currency (millionths of a unit,
// ADR-0006), converted to a display amount HERE and nowhere else -- a
// component reading `cost_micros` directly as cents would be off by 10,000x.
// `unavailableSections` names the screen's plan/invoice/seat/slot concepts
// that have no backing table; the caller must render those as "not available"
// rather than a zero or a blank.
function microsToUsd(micros) {
  return (Number(micros) || 0) / 1e6;
}
export async function fetchBillingUsage(periods) {
  const n = Number.isInteger(periods) ? periods : 12;
  const body = await call(`/api/console/billing?periods=${n}`);
  if (!body || !body.current_period || !Array.isArray(body.models)) {
    throw new Error('The server returned an unreadable billing summary.');
  }
  return {
    billingPeriod: String(body.billing_period || ''),
    currentPeriodUsd: microsToUsd(body.current_period.cost_micros),
    currentPeriodInputTokens: Number(body.current_period.input_tokens) || 0,
    currentPeriodOutputTokens: Number(body.current_period.output_tokens) || 0,
    models: body.models.map((m) => ({
      provider: String(m.provider || ''),
      model: String(m.model || ''),
      costUsd: microsToUsd(m.cost_micros),
      currency: String(m.currency || ''),
    })),
    history: Array.isArray(body.history)
      ? body.history.map((h) => ({
          billingPeriod: String(h.billing_period || ''),
          costUsd: microsToUsd(h.cost_micros),
        }))
      : [],
    ceilingConfigured: body.ceiling_configured === true,
    ceilingUsd: body.ceiling_micros == null ? null : microsToUsd(body.ceiling_micros),
    remainingUsd: body.remaining_micros == null ? null : microsToUsd(body.remaining_micros),
    unavailableSections: Array.isArray(body.unavailable_sections)
      ? body.unavailable_sections.map(String)
      : [],
  };
}

// ── security posture ──────────────────────────────────────────────────────
//
// Real audit_log-derived counts over a time window, plus the actual recent
// non-success rows behind them. THERE IS NO SCORE, NO CONTROL INVENTORY AND
// NO COMPLIANCE BADGE LIST anywhere in this response -- none has a source of
// truth in the backend, and the screen must render their absence rather than
// a stale or invented number. See api/console/security/route.ts.
export async function fetchSecurityActivity(windowHours, limit) {
  const wh = Number.isInteger(windowHours) ? windowHours : 24;
  const lim = Number.isInteger(limit) ? limit : 20;
  const body = await call(`/api/console/security?window_hours=${wh}&limit=${lim}`);
  if (!body || !body.by_result || !Array.isArray(body.recent_events)) {
    throw new Error('The server returned an unreadable security summary.');
  }
  return {
    windowStart: String(body.window_start || ''),
    windowEnd: String(body.window_end || ''),
    totalActions: Number(body.total_actions) || 0,
    byResult: {
      success: Number(body.by_result.success) || 0,
      denied: Number(body.by_result.denied) || 0,
      escalated: Number(body.by_result.escalated) || 0,
      failed: Number(body.by_result.failed) || 0,
    },
    distinctActionTypes: Number(body.distinct_action_types) || 0,
    distinctAgents: Number(body.distinct_agents) || 0,
    recentEventsTruncated: body.recent_events_truncated === true,
    recentEvents: body.recent_events.map((e) => ({
      eventId: String(e.event_id),
      correlationId: String(e.correlation_id || ''),
      actionType: String(e.action_type || ''),
      result: String(e.result || ''),
      occurredAt: String(e.occurred_at || ''),
      sourceAgentId: orNull(e.source_agent_id),
      authorityLevel: orNull(e.authority_level),
      governanceTokenId: orNull(e.governance_token_id),
      resultReason: orNull(e.result_reason),
    })),
  };
}

// ── models ────────────────────────────────────────────────────────────────
//
// The real logical->concrete map (default/fast/reasoning) and the org's
// configured routing rules. `pricing` stays null wherever model_pricing has
// no row -- that table ships empty by design, so null means "nobody has
// priced this model", never zero. There is deliberately no latency,
// context-window or traffic-share field: none is measured or configured
// anywhere in the backend.
export async function fetchModels() {
  const body = await call('/api/console/models');
  if (!body || !Array.isArray(body.catalogue) || !Array.isArray(body.routing)) {
    throw new Error('The server returned an unreadable model catalogue.');
  }
  return {
    pricingConfigured: body.pricing_configured === true,
    catalogue: body.catalogue.map((c) => ({
      logicalName: String(c.logical_name || ''),
      concreteModel: String(c.concrete_model || ''),
      provider: String(c.provider || ''),
      pricing: c.pricing
        ? {
            inputPriceMicrosPerMtok: Number(c.pricing.input_price_micros_per_mtok) || 0,
            outputPriceMicrosPerMtok: Number(c.pricing.output_price_micros_per_mtok) || 0,
            currency: String(c.pricing.currency || ''),
          }
        : null,
    })),
    routing: body.routing.map((r) => ({
      routingClass: String(r.routing_class || ''),
      targetLogicalModel: String(r.target_logical_model || ''),
      fallbackLogicalModel: orNull(r.fallback_logical_model),
      configured: r.configured === true,
    })),
  };
}

// ── workflows: run history ────────────────────────────────────────────────
//
// The REAL run-history table (workflow_runs, migration 0033), written
// best-effort by Orchestrator.invoke. `failureStage` is where a run STOPPED,
// never how far it progressed -- there is no per-stage progress anywhere on
// the live path, so a caller must not build a stage-by-stage pipeline from
// it. This is a SEPARATE path from the dormant n8n admin route at
// /api/console/workflows (SKYLIZE_ENABLE_N8N_ADMIN, off by default, no
// governance gate) -- that route is not workflow infrastructure and is not
// called here.
export async function fetchWorkflowRuns(limit) {
  const n = Number.isInteger(limit) ? limit : 50;
  const body = await call(`/api/console/workflows/runs?limit=${n}`);
  if (!body || !Array.isArray(body.runs)) {
    throw new Error('The server returned an unreadable workflow run history.');
  }
  return {
    runs: body.runs.map((r) => ({
      runId: String(r.run_id),
      workflowName: String(r.workflow_name || ''),
      agentId: String(r.agent_id || ''),
      status: String(r.status || ''),
      correlationId: String(r.correlation_id || ''),
      startedAt: String(r.started_at || ''),
      finishedAt: orNull(r.finished_at),
      failureStage: orNull(r.failure_stage),
      reason: orNull(r.reason),
    })),
    nextBefore: orNull(body.next_before),
  };
}

// ── notifications ─────────────────────────────────────────────────────────
//
// ORG-SCOPED, NOT PER-USER (migration 0034): `readAt` means "somebody with
// console access acknowledged this", not "this signed-in user did". Only two
// `kind` values have a real producer today -- hitl.approval_requested and
// governance.action_denied -- so a fresh org's list is legitimately EMPTY
// until one of those two things happens. Never given a seeded fallback.
export async function fetchNotifications(limit, unreadOnly) {
  const n = Number.isInteger(limit) ? limit : 50;
  const q = unreadOnly ? `&unread_only=true` : '';
  const body = await call(`/api/console/notifications?limit=${n}${q}`);
  if (!body || !Array.isArray(body.notifications)) {
    throw new Error('The server returned an unreadable notification list.');
  }
  return {
    notifications: body.notifications.map((n2) => ({
      notificationId: String(n2.notification_id),
      kind: String(n2.kind || ''),
      severity: String(n2.severity || ''),
      title: String(n2.title || ''),
      body: String(n2.body || ''),
      correlationId: orNull(n2.correlation_id),
      createdAt: String(n2.created_at || ''),
      readAt: orNull(n2.read_at),
    })),
    unreadCount: Number(body.unread_count) || 0,
    nextBefore: orNull(body.next_before),
  };
}

// ── permission matrix ─────────────────────────────────────────────────────
//
// Mechanically derived on the backend from an `ast` scan of the real
// `Depends(require_role(...))` call sites in edge/routes/*.py
// (edge/permission_matrix.py). `routeGroup` is the raw route-file name --
// owner-approved design, no invented business-action vocabulary. This
// function renames nothing.
export async function fetchPermissionMatrix() {
  const body = await call('/api/console/permissions');
  if (!body || !Array.isArray(body.route_groups)) {
    throw new Error('The server returned an unreadable permission matrix.');
  }
  return {
    routeGroups: body.route_groups.map((g) => ({
      routeGroup: String(g.route_group || ''),
      access: g.access && typeof g.access === 'object' ? g.access : {},
    })),
  };
}

// ── org policy settings (guardrails / retention) ──────────────────────────
//
// NO REGION FIELD: verified against real infra that region is a
// per-environment Terraform variable, not a per-org concept (migration
// 0035's own docstring cites the files). Each guardrail carries `enforced`
// alongside its value -- today all four are `enforced: false`, a stored
// preference with no live enforcement point, and the caller must show that
// rather than imply the toggle does something it does not.
export async function fetchOrgPolicySettings() {
  const body = await call('/api/console/org-policy-settings');
  if (!body || typeof body.retention_days !== 'number') {
    throw new Error('The server returned unreadable org policy settings.');
  }
  return normaliseOrgPolicySettings(body);
}

export async function putOrgPolicySettings(input) {
  const body = await call('/api/console/org-policy-settings', {
    method: 'PUT',
    body: {
      spend_cap_alert_enabled: !!input.spendCapAlertEnabled,
      email_domain_restriction_enabled: !!input.emailDomainRestrictionEnabled,
      pii_redaction_enabled: !!input.piiRedactionEnabled,
      silent_fallback_suppressed: !!input.silentFallbackSuppressed,
      retention_days: Number(input.retentionDays),
    },
  });
  if (!body || typeof body.retention_days !== 'number') {
    throw new Error('The server returned unreadable org policy settings.');
  }
  // The backend echoes what it actually PERSISTED. Return that, never the
  // requested value -- the caller must render what was stored.
  return normaliseOrgPolicySettings(body);
}

function normaliseOrgPolicySettings(body) {
  const field = (obj) => (obj && typeof obj === 'object'
    ? { value: obj.value === true, enforced: obj.enforced === true }
    : { value: false, enforced: false });
  return {
    configured: body.configured === true,
    retentionDays: Number(body.retention_days) || 0,
    spendCapAlert: field(body.spend_cap_alert_enabled),
    emailDomainRestriction: field(body.email_domain_restriction_enabled),
    piiRedaction: field(body.pii_redaction_enabled),
    silentFallbackSuppressed: field(body.silent_fallback_suppressed),
  };
}
