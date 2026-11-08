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

---

## Correction 2026-11-08 (OD16-OD19)

approved in chat 2026-11-08, recorded by T1 follow-up 2. This section corrects the
record above; the original text of OD14 is left unchanged on purpose.

**Correction to OD14.** OD14's sentence "Railway stays the active staging." is
WRONG and is superseded by OD16: no working staging environment exists, on Railway
or anywhere else. The rest of OD14 (deploy-staging.yml is workflow_dispatch only)
still stands.

**OD16** No working staging environment exists (evidence: no backend railway.json; last
     Railway deployment 2026-07-27, all inactive; the example URL 404s). Correct OD14's
     "Railway stays the active staging" line in the decision record — do NOT rewrite
     history, append a dated correction. Environment of record for proving behavior,
     until further notice: the CI integration job on real Postgres.

Status: EXECUTED (this correction). Evidence, re-derivable read-only:
- No backend Railway config: the only railway.json files are `website/railway.json`
  (Next.js site) and `infra/opa/railway.json` (OPA); `git ls-tree -r --name-only HEAD | grep -i railway`.
- Last Railway deployment: `gh api "repos/skylizeai-dev/Skylize/deployments?environment=radiant-encouragement%20/%20production"`
  -> newest is 2026-07-27T19:50:13Z (sha 603936a, creator railway-app[bot]); every status checked is `inactive`.
- The example backend URL in `website/.env.local.example:9` (`https://skylize-backend.up.railway.app`)
  returned HTTP 404 "Application not found" on `/health` and `/` (2026-11-07).
- Environment of record: the `integration` job of `.github/workflows/ci.yml` (real Postgres 16 + Redis 7 service containers).
- Not corrected here (hygiene backlog): the header comment of `.github/workflows/deploy-staging.yml`
  still says "Railway stays the active staging environment".

---

**OD17** Standing up an environment is its own topic (T7), NOT a prerequisite of T4
     (Stripe test-mode wiring). Trigger: first pilot or a YC demo date. When triggered,
     one environment only: Railway.

Status: RECORDED (T7 not started; waits for its trigger)

---

**OD18** Delete feat/t1-followup (remote + local) after proving its content is on main
     (`git diff origin/main feat/t1-followup -- <its files>` empty). Add an ignored-build
     step for archive/** on both Vercel projects — if that cannot be done from the repo,
     report it as OWNER-CHECK with the exact dashboard path; do not guess a config key.

Status: branch deletion SCHEDULED for T1 follow-up 2, after this record merges (proof
and result in that report). Vercel ignored-build step: OWNER-CHECK. It is not changed
from this PR, whose owned files are this record only; the Vercel connection available
to T1 could not read the two projects (404), so their current setting is UNVERIFIED.

---

**OD19** The 21:42 reflog event is closed: the 2026-11-07 audit session's own fetch. The
     gc --auto part stays UNVERIFIED and will not be pursued.

Status: CLOSED. The audit session ran `git fetch --all --prune` against the primary
tree at 2026-11-07T18:41:55Z (21:41:55 +03:00). Whether that fetch's automatic
maintenance (gc --auto / reflog expire) rewrote the reflogs is UNVERIFIED and not pursued.

---

## 2026-11-08 (OD26-OD27)

approved in chat 2026-11-08, recorded by T1 follow-up 3

**OD26** Accept tree-equality as merge proof. Delete, remote + local, both branches whose
     tree == main's (921e10b): feat/myday-revocation (local at 2acd81e) and
     feat/staging-record-correction (#40). Re-confirm tree equality per branch
     (`git rev-parse <branch>^{tree}` == main's blob at that path set) before each delete.

Status: EXECUTED. Re-confirmed per branch against its own PR's squash commit, because
main's tree moved from 921e10b to d479a77 when #40 merged:
- origin/feat/myday-revocation (1102f6d) tree 921e10b == #34 merge 5fcc081 tree; its diff to
  main is exactly #40's diff; local 2acd81e was an ancestor of it with 0 unique commits.
- feat/staging-record-correction (b3b3ea5, local == remote) tree d479a77 == #40 merge 0fe978b
  tree == main's tree.
Both deleted remote + local.

---

**OD27** Fix the one self-contradiction OD16 created: deploy-staging.yml:6-7 still says
     "Railway stays the active staging environment". Change it to state no live staging
     exists; environment of record is the CI integration job. One-line workflow-comment
     edit, its own commit, cite OD16/OD27.

Status: EXECUTED (.github/workflows/deploy-staging.yml:6-7 comment changed; triggers and jobs unchanged)

---

## Backlog (tracked)

- [ ] Railway doc wording: website/docs/CONSOLE_BFF.md, website/docs/DEPLOY_RAILWAY.md, docs/audits/tier0_status_audit.md, docs/audits/epic_user_auth_buildout.md, website/.env.local.example, scripts/anytype_sync/.env.example and the code comments in website/src/lib/skylize/config.ts, website/src/lib/skylize/myday-config.ts, website/src/app/console/page.tsx describe the INTENDED Railway setup (OD17) and stay as is; only their present-tense "it exists" phrasing is wrong (OD16). Not worth a sweep pre-pilot; revisit when T7 stands up Railway.
