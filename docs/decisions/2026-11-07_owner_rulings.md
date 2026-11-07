# Owner rulings OD1-OD15 (+ OD3-AMEND)

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

---

## T1 follow-up rulings

approved in chat 2026-11-07, recorded by the T1 follow-up

**OD13** Close PR #30 with comment: "Superseded by the 2026-11-07 governed audit; content
     stale (23 vs 33 contracts). Closed per OD13. Branch kept."

Status: EXECUTED (PR #30 closed with that comment; branch feat/audit-product-readiness kept at 5b57433. Before closing, merge-guard failed on #30 as intended: run 42410383315.)

---

**OD14** Deploy → Staging: trigger = workflow_dispatch ONLY until AWS staging is provisioned
     (remove the push trigger). Railway stays the active staging. Add a header comment
     citing OD14 and listing the 5 provisioning prerequisites from the T1 report.

Status: EXECUTED (.github/workflows/deploy-staging.yml: `push` trigger removed, `workflow_dispatch` kept, header comment lists the five prerequisites)

---

**OD15** Ruleset 26770388 keeps zero bypass actors. Emergency path = the owner edits the
     ruleset in GitHub settings; an owner bypass would also bypass every agent that uses
     the owner's identity.

Status: IN FORCE (ruleset 26770388 "protect-main (OD2)": `bypass_actors: []`; no change needed)

---

**OD3-AMEND** If My Day access-JWT TTL > 15 min: lower it to ≤ 15 min and add BFF
     refresh-on-401 (already a Stage 2 prerequisite) instead of an access-token denylist.

Status: SCHEDULED T2 (amends OD3's "add access-token revocation first")

---

## Process note

T1 merged #37 before CI completed (pre-ruleset); now prevented mechanically by the ruleset.

(Evidence: #37 was merged at 2026-11-07T19:42:08Z in the GitHub UI by skylizeai-dev while `unit` and `integration` were still pending; ruleset 26770388 was created at 19:44:41Z; the post-merge push CI run 42407492854 on a198ae7 succeeded.)
