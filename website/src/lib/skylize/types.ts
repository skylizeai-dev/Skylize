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

/**
 * Retention bounds mirroring the CHECK constraint in migration 0035
 * (`org_policy_settings.retention_days`) and
 * `dal/org_policy_settings.py` (`RETENTION_MIN_DAYS` / `RETENTION_MAX_DAYS`).
 * MIN is the documented compliance floor for audit/governance retention
 * (docs/02_architecture/event_driven_architecture.md section 11: 7 years).
 * MAX is an engineering default, not sourced from any document, pending real
 * compliance input.
 */
export const RETENTION_DAYS_MIN = 2555;
export const RETENTION_DAYS_MAX = 3650;

/**
 * One guardrail value plus whether it is backed by a real enforcement point
 * today (edge/routes/org_policy_settings.py `GuardrailField`). `enforced:
 * false` means this is currently a stored preference only -- toggling it
 * changes no system behavior yet. As of migration 0035 all four guardrails
 * are `enforced: false`; the console should render that honestly rather than
 * imply any of them are wired.
 */
export interface BackendGuardrailField {
  value: boolean;
  enforced: boolean;
}

/** GET|PUT /api/v1/org-policy-settings response (OrgPolicySettingsResponse). */
export interface BackendOrgPolicySettingsResponse {
  spend_cap_alert_enabled: BackendGuardrailField;
  email_domain_restriction_enabled: BackendGuardrailField;
  pii_redaction_enabled: BackendGuardrailField;
  silent_fallback_suppressed: BackendGuardrailField;
  retention_days: number;
  configured: boolean;
}

/** PUT /api/v1/org-policy-settings request body (SetOrgPolicySettingsRequest). */
export interface SetOrgPolicySettingsInput {
  spend_cap_alert_enabled: boolean;
  email_domain_restriction_enabled: boolean;
  pii_redaction_enabled: boolean;
  silent_fallback_suppressed: boolean;
  retention_days: number;
}

/**
 * The three LOGICAL model names the LLM gateway accepts. A CLOSED set: they are
 * exactly the keys of `AnthropicAdapter._model_map`
 * (src/skylize/adapters/llm/anthropic_adapter.py:267-271), and a fourth name
 * raises `ValueError` at `_concrete_model` before any request leaves. The same
 * three are the CHECK constraint in migration 0032.
 *
 * These are NOT product model names. The concrete provider model id each one
 * resolves to arrives on the wire in `concrete_model`, from Settings
 * (config.py:311-313), because it changes with provider releases and must never
 * be hardcoded in the browser.
 */
export const LOGICAL_MODELS = ["default", "fast", "reasoning"] as const;

export type LogicalModel = (typeof LOGICAL_MODELS)[number];

/**
 * One `model_pricing` row (migration 0012), in that table's own units:
 * micro-currency per 1,000,000 tokens, so every real quoted price is an exact
 * integer and no float touches money ($3.00/Mtok == 3_000_000). The console
 * formats this; it does not receive a pre-rounded number.
 */
export interface BackendModelPricing {
  input_price_micros_per_mtok: number;
  output_price_micros_per_mtok: number;
  currency: string;
  pricing_version: number;
}

/**
 * One catalogue entry from GET /api/v1/models (ModelCatalogueEntry).
 *
 * `pricing` is NULL whenever `model_pricing` has no row covering this concrete
 * model. That table is seeded EMPTY by design (migration 0012's Seed note), so
 * null is the EXPECTED value on an unseeded deployment and means "nobody has
 * priced this model". The console must say that out loud; rendering a blank
 * cost cell that reads as zero would be a claim the backend cannot support.
 *
 * There is deliberately no `latency_ms` and no `context_window`: nothing in the
 * backend measures or stores either one (edge/routes/models.py's docstring).
 */
export interface BackendModelCatalogueEntry {
  logical_name: LogicalModel;
  concrete_model: string;
  provider: string;
  pricing: BackendModelPricing | null;
}

/**
 * One routing rule from GET /api/v1/models (ModelRoutingRuleResponse).
 *
 * All three classes are ALWAYS present. `configured: false` means no row exists
 * and this is the fail-closed identity mapping (`target === routing_class`) --
 * exactly what the adapter does today. Display only.
 *
 * There is deliberately no `traffic_share_pct`: traffic share is not a
 * configured value. It is derivable from `ai_cost_ledger`, which holds one
 * immutable row per real provider call, so the only honest version of that
 * number is an aggregate over what actually ran.
 */
export interface BackendModelRoutingRule {
  routing_class: LogicalModel;
  target_logical_model: LogicalModel;
  fallback_logical_model: LogicalModel | null;
  configured: boolean;
}

/** GET /api/v1/models response (ModelsResponse). */
export interface BackendModelsResponse {
  catalogue: BackendModelCatalogueEntry[];
  routing: BackendModelRoutingRule[];
  pricing_configured: boolean;
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

/**
 * GET|PUT /api/console/org-policy-settings. Passed through unchanged from the
 * backend -- every field, including each guardrail's `enforced` flag, is
 * already exactly what the Org Settings screen needs to render honestly.
 * There is deliberately no `region` field: verified against real infra
 * (see migrations/versions/0035_org_policy_settings.py) that region is a
 * per-environment Terraform variable, not a per-org concept.
 */
export type ConsoleOrgPolicySettingsResponse = BackendOrgPolicySettingsResponse;

/**
 * GET /api/console/models. Passed through unchanged from the backend: every
 * field is already exactly what the Models screen needs, and there is nothing
 * to project away because the backend already refuses to send a field it cannot
 * source.
 */
export interface ConsoleModelsResponse {
  catalogue: BackendModelCatalogueEntry[];
  routing: BackendModelRoutingRule[];
  pricing_configured: boolean;
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
 * GET /api/v1/workflows/runs — one row of REAL run history from `workflow_runs`
 * (migration 0033), written best-effort by `Orchestrator.invoke`.
 *
 * `failure_stage` IS NOT PROGRESS. It names where a run STOPPED, and it is null
 * on every run that completed. Nothing on the live orchestrator path records
 * that a run reached a given stage, so a UI must not derive a stage-by-stage
 * pipeline (done / in progress / pending) from this field or from the
 * catalogue's static `stages`.
 */
export interface BackendWorkflowRun {
  run_id: string;
  workflow_name: string;
  agent_id: string;
  /** running | completed | denied | failed */
  status: string;
  correlation_id: string;
  started_at: string;
  finished_at: string | null;
  failure_stage: string | null;
  reason: string | null;
}

/** GET /api/v1/workflows/runs — WorkflowRunListResponse. `next_before` is the
 *  keyset cursor for the next (older) page; null means no more rows. */
export interface BackendWorkflowRunListResponse {
  runs: BackendWorkflowRun[];
  next_before: string | null;
}

/**
 * One row of GET /api/v1/notifications — NotificationResponse
 * (notifications.py). ORG-SCOPED, not per-user (migration 0034): `read_at` is
 * "somebody with console access acknowledged this", not a per-person flag.
 *
 * `kind` is one of exactly two values with a real producer today —
 * "hitl.approval_requested" and "governance.action_denied" — both written by
 * AgentExecutionService at the point the governance gate already knows the
 * fact. There is no scheduled or demo producer behind any other kind.
 */
export interface BackendNotification {
  notification_id: string;
  kind: string;
  severity: string;
  title: string;
  body: string;
  correlation_id: string | null;
  created_at: string;
  read_at: string | null;
}

/** GET /api/v1/notifications — NotificationListResponse. `next_before` is the
 *  cursor for the next (older) page; null means there are no more rows. */
export interface BackendNotificationListResponse {
  notifications: BackendNotification[];
  unread_count: number;
  next_before: string | null;
}

/**
 * GET /api/v1/workflows — one workflow the backend can ACTUALLY run.
 *
 * The backend derives this list from the single graph the orchestrator builds,
 * so it has ONE entry today. A console must render what arrives; padding it to
 * match a mock would advertise triggers that do not exist.
 */
export interface BackendWorkflowDefinition {
  name: string;
  agent_id: string;
  agent_role: string;
  department: string;
  authority_level: string;
  trigger_path: string;
  /** The graph's STATIC node sequence — the workflow's shape, not any run's
   *  position in it. See `stage_progress_supported`. */
  stages: string[];
}

/** GET /api/v1/workflows — WorkflowDefinitionListResponse. */
export interface BackendWorkflowDefinitionListResponse {
  workflows: BackendWorkflowDefinition[];
  /** Always false today. Stated by the backend so a client cannot infer stage
   *  tracking from the presence of `stages`. */
  stage_progress_supported: boolean;
}

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

/**
 * One row of GET /api/v1/knowledge/index-health — SourcePathHealth.
 *
 * `source_path` IS NOT A DATA SOURCE. It is the origin string whoever ingested
 * the document supplied: an upload's filename, the literal
 * "onboarding-interview", or a webhook-supplied path (knowledge.py
 * `upload_knowledge` / `interview_knowledge`). Nothing in the platform
 * registers, connects to, polls or syncs anything named here — there is no
 * connector registry, no sync cadence, and no knowledge of the origin's true
 * size. So there is no connector type, no SYNCED/INDEXING status and no
 * coverage percentage to send, and the console must not render one.
 *
 * `last_ingested_at` is when Skylize last WROTE this path's chunks. It is not
 * a freshness-versus-origin figure: the origin may have changed since, and the
 * platform has no way to know that it did.
 */
export interface BackendKnowledgeSourcePath {
  source_path: string;
  /** Vector points stored under this origin string. */
  chunks: number;
  /** Distinct `parent_doc_id` values — the unit a caller ingested. */
  documents: number;
  departments: string[];
  last_ingested_at: string | null;
}

/**
 * GET /api/v1/knowledge/index-health — IndexHealthResponse.
 *
 * A census of the org's Qdrant index, derived entirely from stored payload
 * fields: every number is a count or a max over points that really exist.
 *
 * `truncated` true means the backend's walk hit its point cap, so every count
 * here is a LOWER BOUND over a prefix of the tenant's points. The console must
 * surface that rather than present the totals as complete.
 */
export interface BackendKnowledgeIndexHealth {
  total_chunks: number;
  total_documents: number;
  source_paths: BackendKnowledgeSourcePath[];
  last_ingested_at: string | null;
  truncated: boolean;
}

/**
 * GET /api/v1/billing/usage — BillingUsageResponse (edge/routes/billing.py).
 *
 * MONEY UNIT: every `*_micros` field is MICRO-currency — millionths of one
 * currency unit, the unit `ai_cost_ledger.cost_micros` and
 * `org_spend_ceiling.ceiling_micros` store (ADR-0006). It is NOT cents. To show
 * a currency amount, divide by 1_000_000; to show cents, divide by 10_000. A
 * component that treats one of these as cents is off by 10,000x.
 *
 * WHAT IS NOT HERE. There is no plan tier, no invoice list, no seat count and
 * no agent-slot count — those have NO backing table anywhere in this repo, so
 * the response has no field for them rather than a null that would read as
 * "unset". `unavailable_sections` names them; the Billing screen must render
 * each as explicitly unavailable and must NOT substitute a placeholder.
 */
export interface BackendModelUsage {
  provider: string;
  model: string;
  /** Micro-currency, NOT cents. Charges net of reversals. */
  cost_micros: number;
  input_tokens: number;
  output_tokens: number;
  /** Read from the ledger rows — never assumed to be USD. */
  currency: string;
}

export interface BackendPeriodUsage {
  /** Calendar month as "%Y-%m", e.g. "2026-07". */
  billing_period: string;
  /** Micro-currency, NOT cents. */
  cost_micros: number;
  input_tokens: number;
  output_tokens: number;
}

export interface BackendBillingUsage {
  billing_period: string;
  /** All-zero is a REAL answer, not a loading state — see billing.py. */
  current_period: BackendPeriodUsage;
  models: BackendModelUsage[];
  /** Newest first, including the current month. */
  history: BackendPeriodUsage[];
  /** False => the LLM egress gate fails closed; no ceiling has been set. */
  ceiling_configured: boolean;
  /** GOVERNANCE cap (migration 0014), NOT a plan allowance. Micro-currency. */
  ceiling_micros: number | null;
  /** May be NEGATIVE — the ceiling is a soft cap with bounded overshoot. */
  remaining_micros: number | null;
  /** Billing-screen sections with no backend: render as unavailable. */
  unavailable_sections: string[];
  detail: string | null;
}

export type ConsoleAuditList = BackendAuditListResponse;
export type ConsoleKnowledgeIndexHealth = BackendKnowledgeIndexHealth;
export type ConsoleNotificationList = BackendNotificationListResponse;
export type ConsoleNotification = BackendNotification;
export type ConsoleCoworkTurn = BackendCoworkTurnResponse;
export type ConsoleApiKeyList = BackendApiKey[];
export type ConsoleIssuedApiKey = BackendIssuedApiKey;
export type ConsoleOrgUserList = BackendOrgUser[];
/** GET /api/console/billing — forwarded verbatim; nothing to project away. */
export type ConsoleBillingUsage = BackendBillingUsage;
export type ConsoleWorkflowRunList = BackendWorkflowRunListResponse;
export type ConsoleWorkflowDefinitionList = BackendWorkflowDefinitionListResponse;

/**
 * GET /api/v1/permissions/matrix — PermissionMatrixResponse
 * (src/skylize/edge/routes/permissions.py, src/skylize/edge/permission_matrix.py).
 *
 * MECHANICALLY DERIVED, NOT HAND-TRANSCRIBED. Every field here is a direct
 * rendering of an `ast` scan over `src/skylize/edge/routes/*.py`: `route_group`
 * is the route file's own module name (never a business-action label), and
 * `access[role].read`/`.write` is true only when a REAL
 * `Depends(require_role(...))` / `Depends(require_any_role(...))` /
 * `Depends(require_any_role_or_user(...))` call site in that file names that
 * role on a GET/HEAD (`read`) or POST/PUT/PATCH/DELETE (`write`) route. A route
 * group with zero role-gated routes (e.g. "auth", "knowledge") still appears,
 * with every role false — its presence says the scanner looked, not that a
 * gate exists.
 *
 * `generated_at` is the timestamp of THIS scan; the backend re-parses the
 * route tree on every request rather than caching, so there is no staler
 * cached copy to distrust.
 */
export interface BackendRoleAccess {
  read: boolean;
  write: boolean;
}

export interface BackendRouteGroup {
  route_group: string;
  route_count: number;
  /** Keyed by role name; always exactly the 5 platform roles. */
  access: Record<string, BackendRoleAccess>;
}

export interface BackendPermissionMatrixResponse {
  /** UTC ISO-8601 timestamp of this scan. */
  generated_at: string;
  /** The 5 platform roles, in column order. */
  roles: string[];
  route_groups: BackendRouteGroup[];
}

export type ConsolePermissionMatrix = BackendPermissionMatrixResponse;
