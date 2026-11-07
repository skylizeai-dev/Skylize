# Owner rulings OD1-OD12

approved in chat 2026-11-07, recorded by T1

Rulings are reproduced verbatim. Each is followed by its status as of the T1 hygiene run.

---

**OD1**  Merge #35 (squash) and watch Deploy → Staging, subject to REVIEW step 4.

Status: EXECUTED (merge) / deploy BLOCKED on owner infra actions: see REPO_STATE (PR #35 squash-merged as 19b149e1ae8c630206df5f8bdb7acca6133fbd64; Deploy -> Staging run 42406222420 = failure)

---

**OD2**  Solo founder, so no required approvals. Protect main with a ruleset: PR required;
     required status checks = all CI jobs + merge-guard; branch must be up to date;
     block force-push and deletion. Add a `merge-guard` check that FAILS when the PR
     title matches /do not merge/i or the PR has label `hold`.

Status: EXECUTED IN T1 (merge-guard workflow PR #36 merged as 93ff73fbce67ece77a64db705edf6667c978ee00; main ruleset applied by the T1 orchestrator after this PR)

---

**OD3**  #34: merge via independent reviewer + owner summary approval; if access-JWT
     TTL > 15 min, add access-token revocation first. [executed in T2]

Status: SCHEDULED T2

---

**OD4**  HubSpot create_contact: no retry on 5xx/timeout. [T3]

Status: SCHEDULED T3

---

**OD5**  Tenant OAuth connect: one generic, provider-registry-driven authorize/callback broker. [later]

Status: LATER

---

**OD6**  Ungoverned agents get lifecycle_status "planned"; Console shows governed agents by default. [later]

Status: LATER

---

**OD7**  ADR-0004 re-scoped: inline engine is the production arbiter; OPA is roadmap. [later]

Status: LATER. Note: this ruling RE-SCOPES ADR-0004, but T1 did NOT edit
docs/architecture/adr/0004-opa-production-arbiter.md or the ARCHITECTURE
CONSTRAINTS section of CLAUDE.md; both still carry the old ADR-0004 wording
until the later work lands.

---

**OD8**  Stripe: wire in test mode behind a flag, off in production. [later]

Status: LATER

---

**OD9**  My Day Stage 2 surface = the SPA, conditional on same-origin serving with the BFF. [later]

Status: LATER

---

**OD10** Login rate limit: Redis, keyed on normalized email + trusted client IP. [later]

Status: LATER

---

**OD11** Console identity: interim now; human-JWT once My Day's path is proven. [later]

Status: LATER

---

**OD12** Hygiene: archive local-only branches and orphans as `archive/*` remote refs, no
     deletion; regenerate REPO_STATE.md from derived facts; move
     docs/03_agents/_137_agent_evidence_audit.md to docs/audits/; close stale PRs #1 and
     #5 with a comment (branches kept).

Status: EXECUTED IN T1 (archive refs pushed; REPO_STATE regenerated; _137 doc moved; #1 and #5 closed by the orchestrator)
