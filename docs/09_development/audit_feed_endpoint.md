# Audit feed endpoint — console handoff (M2.2)

Added in the launch-plan M2.1 backend work. This is the REAL data source that
replaces the fabricated `log-feed.tsx` ("LIVE" mock) on the dashboard and
`/console/logs`.

## Endpoint

`GET /api/v1/audit` — roles: `owner` or `admin` (viewer → 403). Org-scoped by
the caller's context; RLS enforces isolation in Postgres.

Query params:
- `limit` — 1..200, default 50
- `before` — ISO 8601 timezone-aware datetime; returns entries strictly older
  (use `next_before` from the previous page)

Response:
```json
{
  "entries": [
    {
      "event_id": "uuid",
      "correlation_id": "uuid",
      "action_type": "agent.executed | governance.budget_exceeded | governance.tool_call_denied | governance.tool_loop_exceeded | tool.invoked | orchestrator.run | ...",
      "result": "success | denied | escalated | failed",
      "occurred_at": "2026-07-08T12:00:00Z",
      "source_agent_id": "cfo_agent",
      "authority_level": "worker",
      "governance_token_id": "uuid | null",
      "result_reason": "string | null",
      "inputs_hash": "sha256 | null",
      "outputs_hash": "sha256 | null"
    }
  ],
  "next_before": "ISO datetime | null"
}
```

Notes for the UI:
- Newest-first. `next_before == null` means no more pages.
- Payloads are SHA-256 hashes only — render provenance (who/what/when/verdict),
  never pretend to show content.
- `governance_token_id` is a real minted P-384 token id on governed runs —
  linking it in the UI is the "CFO Test" money shot.
- Suggested BFF passthrough: `/api/console/audit` via the shared guarded
  handler (same pattern as `/api/console/workflows`).

Backend tests: `tests/unit/test_audit_routes.py`.
