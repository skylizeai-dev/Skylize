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
