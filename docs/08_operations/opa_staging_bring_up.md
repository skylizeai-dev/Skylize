# OPA Staging Bring-Up Checklist

**Status: DOCUMENTATION OF MANUAL STEPS. Nothing here has been executed.**

Written 2026-07-21 against commit `bf3d8dcb` (branch `feat/durable-governance`). Every
step cites the file it is derived from. Where something is absent, this document says
"verified absent" and names the search, rather than describing a file that does not exist.

This describes what a human must do to stand up a staging OPA server and flip
`SKYLIZE_DECISION_ENGINE` to `opa`. **Following it end-to-end today would produce a
staging environment that denies every proposal**, for the reasons in §0.

---

## §0 — Two blockers that no amount of infrastructure work removes

Read this section before doing anything in §1.

### 0.1 There is no real policy content

All seven policy files are fail-closed placeholders. Every one declares
`default allow := false`, and **not one contains an `allow := true` or any conditional
allow rule**:

| File | Line |
|---|---|
| `policy/skylize/decision/authority.rego` | 13 |
| `policy/skylize/decision/brand_legal.rego` | 13 |
| `policy/skylize/decision/data_access.rego` | 13 |
| `policy/skylize/decision/decision.rego` | 14 |
| `policy/skylize/decision/external_action.rego` | 13 |
| `policy/skylize/decision/security_veto.rego` | 14 |
| `policy/skylize/decision/spend.rego` | 13 |

Authoring real content is gated on owner approval of
`docs/04_decision_engine/policy_inputs.md`. **That file is not tracked in git** — see
`OWNER_DECISIONS_QUEUE_2026-07-21.md` D1. Its own banner (line 3) reads
`Status: DRAFT — AWAITING OWNER APPROVAL`, and no section reads `[APPROVED]`.

### 0.2 The spend fields do not reach OPA

Even with real Rego, the policy would not see the numbers it needs to judge. The consumer
sets `DecisionContext.payload` to the entire event envelope
(`src/skylize/decision_engine/consumer.py:229-240`), so business fields sit one level down
under `payload["payload"]`. `OPAClient._build_input` filters that dict against
`SAFE_PAYLOAD_KEYS` at the **top level only**
(`src/skylize/decision_engine/opa_client.py:61-65`).

For a real `sales.campaign_proposed`, what survives is exactly:

```
{"authority_level": ..., "governance_token_id": ...}
```

`campaign_id`, `channel`, `currency` and `proposed_budget_minor_units` are all dropped.
`guardrails.md` §4 names `amount` among the inputs OPA reads; **no event in the tracked
vocabulary has a field called `amount`** — the spend-bearing field is
`proposed_budget_minor_units`, which is not in `SAFE_PAYLOAD_KEYS`.

This is locked by a characterization test that asserts the current wrong behaviour:
`tests/decision_engine/test_opa_client.py::test_real_event_loses_its_spend_fields_before_reaching_opa`.
Fixing it needs the amount/currency model that `policy_inputs` §0.2 marks
`[OWNER-DECISION-REQUIRED]` (minor units vs major; USD-only vs multi-currency).

---

## §1 — What already exists, and what does not

### Exists

- **OPA server image for Railway** — `infra/opa/Dockerfile` (`openpolicyagent/opa:1.18.2`,
  `COPY policy /policies`, serves `:8181`) and `infra/opa/railway.json`
  (`healthcheckPath: /health`). The bundle is **baked in at build time**, so a policy
  change requires a rebuild and redeploy (`infra/opa/Dockerfile:4-6`).
- **Local OPA service** — `infra/docker-compose.yml:42-52`. Ungated: it starts on a plain
  `docker compose up`.
- **Dormant worker service** — `infra/docker-compose.yml:112-144`, gated behind
  `profiles: ["opa-engine"]` (line 113), so `docker compose up` never starts it.
  This is the only place in the repository where `SKYLIZE_DECISION_ENGINE` is set to
  `opa` (line 120).
- **Worker entrypoint** — `src/skylize/decision_engine/worker.py`; refuses to start unless
  the flag reads `opa` (worker.py:78-83), raising before it opens any pool.
- **Interlock on the other side** — `bootstrap` refuses to build the inline engine when
  the flag reads `opa` (`src/skylize/bootstrap.py:251`). Both sides fail closed, so the
  two engines cannot both emit terminal `decision.*` events.

### Verified absent

Searched every `*.yml, *.yaml, *.json, *.tf, *.ps1, *.sh, *.toml, *.example, Dockerfile*`
in the repository (excluding `.git`, `node_modules`, `.claude`) for
`SKYLIZE_DECISION_ENGINE` and `decision_engine_org_ids`. The **only** hits are
`infra/docker-compose.yml` lines 93 (a comment), 120 and 124.

Specifically **absent from every real deploy path**:

- `.github/workflows/deploy-staging.yml` — deploys one ECS service,
  `skylize-staging-api` (lines 15-16). No worker service, no OPA.
- `infra/terraform/staging/modules/ecs/main.tf` — the task definition declares a **single
  container named `api`** (line 62). Its environment is `SKYLIZE_BACKEND`,
  `SKYLIZE_DEV_AUTH`, `PYTHONPATH` (lines 74-76); its secrets are `SKYLIZE_DB_URL`,
  `SKYLIZE_DB_APP_URL`, `SKYLIZE_REDIS_URL`, `SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET`,
  `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` (lines 81-97). **No `SKYLIZE_OPA_URL`, no
  `SKYLIZE_DECISION_ENGINE`, and no second container.**
- No Terraform resource anywhere provisions an OPA service.

### A related defect worth fixing before anyone trusts `SKYLIZE_OPA_URL`

`infra/docker-compose.yml:80` sets `SKYLIZE_OPA_URL: http://opa:8181` on the **gateway**
service. The main `Settings` class has **no `opa_url` field** (searched
`src/skylize/config.py` for `opa`: the only hits are the comment block at lines 94-105 and
`decision_engine` at line 107). That variable is therefore read by nothing. Only
`DecisionEngineSettings` (`src/skylize/decision_engine/config.py`) has `opa_url`, and only
the worker process uses it. The compose line is harmless but misleading.

---

## §2 — The manual steps

Ordered. Do not start §2 until §0 is resolved; steps 1-4 are safe and reversible on their
own, step 5 is the irreversible one.

1. **Approve the policy inputs.** Track `policy_inputs.md` in git (D1), then move each
   section's banner to `[APPROVED]`. Real Rego authoring is blocked until then.

2. **Fix the OPA input contract** (§0.2). Until this lands, OPA is judging an action it
   cannot see the amount of. Rewrite the characterization test named in §0.2 to assert the
   corrected shape.

3. **Author the real policy bundle.** Replace the seven placeholders. Keep every
   `default allow := false`; add only explicit allow rules traceable to an `[APPROVED]`
   section.

4. **Stand up the OPA server.**
   - *Railway path:* deploy `infra/opa/railway.json` (builds `infra/opa/Dockerfile`;
     Railway root directory must be `/`, per `infra/opa/Dockerfile:9-11`). Health check is
     `GET /health`.
   - *ECS path:* **does not exist yet.** A second container or service, plus the
     `SKYLIZE_OPA_URL` wiring, must be added to
     `infra/terraform/staging/modules/ecs/main.tf`. There is no template to copy.
   - Verify from outside: the two integration tests in
     `tests/decision_engine/test_opa_client_integration.py` run only when
     `SKYLIZE_TEST_OPA_URL` is set (that file, lines 29-36). They assert **deny**, which is
     the correct result against the placeholder bundle.

5. **Deploy the worker and flip the flag.** Add a worker container/service with
   `SKYLIZE_DECISION_ENGINE=opa`, `SKYLIZE_DECISION_ENGINE_ORG_IDS` (JSON array — the
   consumer raises rather than idling on an empty list,
   `src/skylize/decision_engine/consumer.py:160-163`), `SKYLIZE_DATABASE_URL` (note: **not**
   `SKYLIZE_DB_URL`; different settings class, no overlap — `decision_engine/config.py:30`),
   `SKYLIZE_REDIS_URL`, `SKYLIZE_OPA_URL`, and the two Langfuse keys, which have no
   defaults (`decision_engine/config.py:26-27`).

   Flipping the flag on the API side simultaneously stops the inline engine
   (`bootstrap.py:251`). Exactly one engine emits terminal `decision.*` events per
   environment (ADR-0004 §Decision 2,
   `docs/architecture/adr/0004-opa-production-arbiter.md:37`).

---

## §3 — What has never been tested against a real OPA process

Stated plainly so nobody reads green CI as coverage of this:

- **No OPA server has ever been contacted by this codebase.** All 26 pipeline-level OPA
  tests mock `OPAClient.evaluate`; the only tests that would reach a real process are the
  two skip-guarded integration tests above.
- The fail-closed branches — timeout (`opa_client.py:108`), unreachable (`:112`), non-200
  (`:126`), malformed body (`:133`), non-object envelope (`:144`), non-dict result
  (`:153`) — are covered by unit tests, the newest of which use `httpx.MockTransport`.
  Real bytes, but not a real server.
- Consequently: the client's behaviour against a **real** OPA under failure is inferred
  from mocks, not observed. Step 4 above is the first time that assumption gets tested.
