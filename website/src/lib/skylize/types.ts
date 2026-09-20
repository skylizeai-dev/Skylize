// Types derived from the CONFIRMED FastAPI backend shapes (Step-1 review of
// the Python source — do not edit these to match wishes, only to match code):
//
//   src/skylize/edge/gateway.py            -> GET  /health
//   src/skylize/edge/routes/tenants.py     -> GET  /api/v1/tenants/me (TenantResponse)
//   src/skylize/edge/routes/workflows.py   -> POST /api/v1/workflows/creative (WorkflowResponse)
//   src/skylize/edge/routes/kill_switch.py -> POST /api/v1/kill-switch/engage
//   src/skylize/edge/routes/autonomy.py   -> GET/PUT /api/v1/autonomy
//
// Pydantic serializes UUID -> string and None -> null, so optional-with-default
// backend fields appear here as `... | null`, always present in the JSON.

/** GET /health */
export interface BackendHealth {
  status: string;
  backend: string;
}

/** GET /api/v1/tenants/me — TenantResponse */
export interface TenantMe {
  org_id: string;
  display_name: string;
  status: string;
}

/**
 * POST /api/v1/workflows/creative request body (CreativeRunRequest).
 * The backend model is extra="forbid": send exactly these fields.
 * (`brief_id` is accepted upstream but intentionally not exposed via the BFF.)
 */
export interface CreativeRunInput {
  product: string;
  audience: string;
  count?: number;
}

/**
 * POST /api/v1/workflows/creative response (WorkflowResponse).
 * On a successful creative run, `output.hooks` is a string[] (confirmed in
 * src/skylize/app/orchestrator/runner.py and schemas/agents/creative.py).
 */
export interface BackendWorkflowResponse {
  status: string;
  agent_id: string;
  correlation_id: string;
  token_id: string | null;
  event_type: string | null;
  output: Record<string, unknown> | null;
  reason: string | null;
}

/** Scope types the backend asserts on (kill_switch.py `_SCOPES`). */
export type KillSwitchScopeType = "agent" | "department" | "tenant" | "platform";

/** POST /api/v1/kill-switch/engage request body (KillSwitchRequest, extra="forbid"). */
export interface KillSwitchEngageInput {
  scope_type: KillSwitchScopeType;
  scope_id: string;
  reason: string;
}

/** POST /api/v1/kill-switch/engage response. */
export interface KillSwitchEngageResponse {
  status: string;
  scope_type: string;
  scope_id: string;
}

/**
 * The five named autonomy modes, least to most autonomous
 * (src/skylize/contracts/base.py `AutonomyMode`). A CLOSED set: the backend
 * rejects anything else at the Pydantic boundary, in the DAL, and at the
 * table's CHECK constraint. The ORDER is meaningful and matches the backend's
 * AUTHORITY ladder -- do not re-sort it.
 */
export const AUTONOMY_MODES = [
  "observe",
  "propose",
  "act_within_budget",
  "act_and_reallocate",
  "act_governed",
] as const;

export type AutonomyMode = (typeof AUTONOMY_MODES)[number];

/**
 * The fail-closed resolution of an org that has never set a mode
 * (`DEFAULT_AUTONOMY_MODE`, contracts/base.py:55). Ruling 7: absence of a
 * choice means the mode where every action needs a human.
 */
export const DEFAULT_AUTONOMY_MODE: AutonomyMode = "observe";

/** GET|PUT /api/v1/autonomy response (AutonomyModeResponse). */
export interface BackendAutonomyResponse {
  mode: AutonomyMode;
  /**
   * False when no row resolves for the org, i.e. `mode` is the fail-closed
   * default rather than a choice. DISPLAY ONLY -- enforcement acts on `mode`
   * either way (autonomy.py:43-47).
   */
  configured: boolean;
}

/** PUT /api/v1/autonomy request body (SetAutonomyModeRequest). */
export interface SetAutonomyModeInput {
  mode: AutonomyMode;
}

// ---------------------------------------------------------------------------
// BFF response shapes exposed to the browser (FROZEN CONTRACT — the console UI
// is built against these exact names; do not rename).
// ---------------------------------------------------------------------------

/** GET /api/console/health */
export interface ConsoleHealth {
  status: string;
  backend: string;
}

/** GET /api/console/tenant */
export interface ConsoleTenant {
  org_id: string;
  display_name: string;
  status: string;
}

/** POST /api/console/workflows/creative */
export interface ConsoleCreativeResponse {
  status: string;
  hooks: string[];
}

/** POST /api/console/kill-switch */
export interface ConsoleKillSwitchResponse {
  status: string;
}

/**
 * GET|PUT /api/console/autonomy. Passed through unchanged from the backend --
 * both fields are already exactly what the console needs, so there is nothing
 * to project away.
 */
export interface ConsoleAutonomyResponse {
  mode: AutonomyMode;
  configured: boolean;
}

// ---------------------------------------------------------------------------
// Governance surface (agents / HITL / deliverables) — CONFIRMED against the
// FastAPI source the same way as above:
//
//   src/skylize/edge/routes/agents.py       -> GET /api/v1/agents (AgentListResponse)
//                                              POST /api/v1/agents/execute
//                                              (ExecuteAgentResponse on 201; the
//                                              202 body is the route's literal
//                                              JSONResponse content; 403 is an
//                                              HTTPException detail)
//   src/skylize/edge/routes/hitl.py         -> GET /api/v1/hitl (HitlListResponse)
//                                              POST /api/v1/hitl/{id}/approve
//                                              (HitlApproveResponse)
//                                              POST /api/v1/hitl/{id}/reject
//                                              (HitlRejectResponse)
//   src/skylize/edge/routes/deliverables.py -> GET /api/v1/deliverables/{id}
//                                              (DeliverableDetailResponse)
// ---------------------------------------------------------------------------

/** One entry of GET /api/v1/agents — AgentInfo. `input_schema` is the agent's
 *  Pydantic input model as JSON Schema (model_json_schema()). */
export interface BackendAgentInfo {
  agent_id: string;
  name: string;
  description: string;
  department: string;
  authority_level: string;
  input_schema: Record<string, unknown>;
}

/** GET /api/v1/agents — AgentListResponse. */
export interface BackendAgentListResponse {
  agents: BackendAgentInfo[];
}

/** POST /api/v1/agents/execute, HTTP 201 — ExecuteAgentResponse. */
export interface BackendExecuteApproved {
  deliverable_id: string;
  status: string;
  agent_id: string;
  title: string;
}

/** POST /api/v1/agents/execute, HTTP 202 — the route's literal JSONResponse
 *  content (agents.py, AgentDeferredToHuman branch). */
export interface BackendExecuteDeferred {
  hitl_id: string;
  status: "deferred_to_human";
  agent_id: string;
  reason: string;
}

/** One entry of GET /api/v1/hitl — HitlItemResponse. */
export interface BackendHitlItem {
  hitl_id: string;
  agent_id: string | null;
  trigger_reason: string;
  status: string;
  created_at: string;
  expires_at: string | null;
  proposal_summary: Record<string, unknown>;
  request_input: Record<string, unknown> | null;
}

/** GET /api/v1/hitl — HitlListResponse (PaginationMeta inline). */
export interface BackendHitlListResponse {
  data: BackendHitlItem[];
  pagination: {
    total: number;
    offset: number;
    limit: number;
    has_more: boolean;
  };
}

/** POST /api/v1/hitl/{id}/approve, HTTP 200 — HitlApproveResponse. */
export interface BackendHitlApproveResponse {
  hitl_id: string;
  status: string;
  deliverable_id: string;
  agent_id: string;
  title: string;
}

/** POST /api/v1/hitl/{id}/reject, HTTP 200 — HitlRejectResponse. */
export interface BackendHitlRejectResponse {
  hitl_id: string;
  status: string;
}

/** GET /api/v1/deliverables/{id} — DeliverableDetailResponse. */
export interface BackendDeliverableDetail {
  id: string;
  org_id: string;
  agent_id: string;
  deliverable_type: string;
  title: string;
  status: string;
  version: number;
  summary: string | null;
  parent_id: string | null;
  created_at: string;
  updated_at: string;
  approved_at: string | null;
  approved_by: string | null;
  content_markdown: string;
  metadata_json: Record<string, unknown>;
  governance_token_id: string | null;
}

// The new console governance endpoints are verbatim pass-throughs of the
// backend shapes above (status codes included), so the browser-facing types
// are aliases — the frozen contract is the backend model itself.
export type ConsoleAgentList = BackendAgentListResponse;
export type ConsoleExecuteApproved = BackendExecuteApproved;
export type ConsoleExecuteDeferred = BackendExecuteDeferred;
export type ConsoleHitlList = BackendHitlListResponse;
export type ConsoleHitlItem = BackendHitlItem;
export type ConsoleHitlApprove = BackendHitlApproveResponse;
export type ConsoleHitlReject = BackendHitlRejectResponse;
export type ConsoleDeliverable = BackendDeliverableDetail;

// ---------------------------------------------------------------------------
// Console wiring, round 2 — audit / cowork / api-keys / org users.
//
// Confirmed against the Python source, same discipline as above:
//   src/skylize/edge/routes/audit.py    -> GET  /api/v1/audit (AuditListResponse)
//   src/skylize/edge/routes/cowork.py   -> POST /api/v1/cowork/turns
//                                          (CoworkTurnResponse, 201)
//   src/skylize/edge/routes/api_keys.py -> GET/POST/DELETE /api/v1/api-keys
//   src/skylize/edge/routes/tenants.py  -> GET  /api/v1/tenants/me/users
// ---------------------------------------------------------------------------

/**
 * One row of GET /api/v1/audit — AuditEntryResponse (audit.py:29-40).
 *
 * THERE IS NO HUMAN ACTOR FIELD AND NO SIGNATURE FIELD, and the console must
 * not imply either. `source_agent_id` is an AGENT id or null — never a person.
 * `inputs_hash`/`outputs_hash` are SHA-256 CONTENT HASHES of the payloads
 * (audit.py:8-10), which prove the payload has not changed; they are not
 * signatures and prove nothing about who produced the row.
 */
export interface BackendAuditEntry {
  event_id: string;
  correlation_id: string;
  action_type: string;
  result: string;
  occurred_at: string;
  source_agent_id: string | null;
  authority_level: string | null;
  governance_token_id: string | null;
  result_reason: string | null;
  inputs_hash: string | null;
  outputs_hash: string | null;
}

/** GET /api/v1/audit — AuditListResponse. `next_before` is the cursor for the
 *  next (older) page; null means there are no more rows. */
export interface BackendAuditListResponse {
  entries: BackendAuditEntry[];
  next_before: string | null;
}

/**
 * One row of `recent_events` in GET /api/v1/security/activity —
 * GovernanceEventResponse (edge/routes/security.py).
 *
 * Same two absences as `BackendAuditEntry`, for the same reasons: there is no
 * human actor behind `source_agent_id`, and the content hashes that would be
 * the nearest thing to a signature are not carried on this route at all.
 */
export interface BackendGovernanceEvent {
  event_id: string;
  correlation_id: string;
  action_type: string;
  result: string;
  occurred_at: string;
  source_agent_id: string | null;
  authority_level: string | null;
  governance_token_id: string | null;
  result_reason: string | null;
}

/**
 * GET /api/v1/security/activity — SecurityActivityResponse.
 *
 * THERE IS NO SCORE FIELD, NO CONTROLS ARRAY AND NO COMPLIANCE BADGE ARRAY, and
 * the console must not synthesize any of them. The mocked security screen
 * carried a hardcoded posture score of 94, eight named controls (SSO, SCIM,
 * encryption at rest, data residency, HITL, PII redaction, sandbox isolation,
 * pen test) and four compliance badges (SOC 2, ISO 27001, GDPR, HIPAA-READY).
 * NONE of the three has a source of truth anywhere in the backend — no table,
 * no scoring methodology, no auditor. They were deliberately not built, and
 * their absence here is the honest signal the screen should render.
 *
 * What IS real: counts of every audited outcome over the stated window, and the
 * actual most recent non-success rows from the append-only `audit_log`.
 */
export interface BackendSecurityActivityResponse {
  window_start: string;
  window_end: string;
  total_actions: number;
  /** Keyed by audit result — success, denied, escalated, failed. Always all four. */
  by_result: Record<string, number>;
  distinct_action_types: number;
  distinct_agents: number;
  /** Newest-first sample of the non-success rows; bounded by `limit`. */
  recent_events: BackendGovernanceEvent[];
  /** True when the window holds more non-success rows than were returned. */
  recent_events_truncated: boolean;
}

export type ConsoleSecurityActivity = BackendSecurityActivityResponse;

/**
 * POST /api/v1/cowork/turns request body — CoworkTurnIn.
 *
 * `message` IS THE ONLY FIELD, and that is a design decision rather than an
 * omission: the model is extra="forbid", the agent is the module constant
 * COWORK_AGENT_ID, and the principal is the caller's own `ctx.user_id`
 * (cowork.py:15-27, 47-50 — "a caller must never be able to name someone else
 * as the principal"). Adding an agent/department field here would have to
 * widen the backend, which is explicitly out of bounds.
 */
export interface CoworkTurnInput {
  message: string;
}

/** POST /api/v1/cowork/turns, HTTP 201 — CoworkTurnResponse. */
export interface BackendCoworkTurnResponse {
  reply: string;
  deliverable_id: string;
  agent_id: string;
}

/** POST /api/v1/cowork/turns, HTTP 202 — the route's literal JSONResponse
 *  content (cowork.py, AgentDeferredToHuman branch). A turn that hit a gated
 *  condition produced a HITL row instead of a reply. */
export interface BackendCoworkTurnDeferred {
  hitl_id: string;
  status: "deferred_to_human";
  agent_id: string;
  reason: string;
}

/** One row of GET /api/v1/api-keys — KeyResponse. Carries NO secret and no
 *  hash: the plaintext exists in exactly one response ever, the 201 below. */
export interface BackendApiKey {
  key_id: string;
  prefix: string;
  name: string;
  scopes: string[];
  created_by: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** POST /api/v1/api-keys request body — IssueKeyRequest (extra="forbid"). */
export interface IssueApiKeyInput {
  name: string;
  scopes?: string[];
  expires_in_days?: number | null;
}

/**
 * POST /api/v1/api-keys, HTTP 201 — IssuedKeyResponse.
 *
 * `api_key` is the PLAINTEXT SECRET and is present in this one response and
 * never again (api_keys.py:6-8). It must reach the operator once and must not
 * be persisted anywhere by the console.
 */
export interface BackendIssuedApiKey {
  key_id: string;
  prefix: string;
  name: string;
  scopes: string[];
  api_key: string;
  expires_at: string | null;
}

/**
 * One row of GET /api/v1/tenants/me/users — UserResponse.
 *
 * TWO FIELDS, AND THAT IS ALL THE BACKEND HAS. There is no email, no display
 * name, no last-seen timestamp and no MFA state anywhere behind this route
 * (tenants.py:105-113), so a console column for any of them could only be
 * filled with fiction.
 */
export interface BackendOrgUser {
  user_id: string;
  role: string;
}

export type ConsoleAuditList = BackendAuditListResponse;
export type ConsoleCoworkTurn = BackendCoworkTurnResponse;
export type ConsoleApiKeyList = BackendApiKey[];
export type ConsoleIssuedApiKey = BackendIssuedApiKey;
export type ConsoleOrgUserList = BackendOrgUser[];
