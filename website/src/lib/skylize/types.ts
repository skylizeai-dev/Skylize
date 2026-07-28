// Types derived from the CONFIRMED FastAPI backend shapes (Step-1 review of
// the Python source — do not edit these to match wishes, only to match code):
//
//   src/skylize/edge/gateway.py            -> GET  /health
//   src/skylize/edge/routes/tenants.py     -> GET  /api/v1/tenants/me (TenantResponse)
//   src/skylize/edge/routes/workflows.py   -> POST /api/v1/workflows/creative (WorkflowResponse)
//   src/skylize/edge/routes/kill_switch.py -> POST /api/v1/kill-switch/engage
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
