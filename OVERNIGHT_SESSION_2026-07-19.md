# Overnight Session — 2026-07-19

**Role:** Principal Engineer + Release Engineer (unattended overnight session).
**Owner:** asleep, cannot answer — judgment calls are QUEUED, not guessed (see `OWNER_DECISIONS_QUEUE_2026-07-19.md`).

> Every claim below cites `file:line` or a commit SHA. No unverified assertions.
> "no tracked source" is written wherever a reference could not be confirmed in-repo.

---

## Starting state (verified)

| Fact | Value | How verified |
|------|-------|--------------|
| Branch | `feat/durable-governance` | `git rev-parse --abbrev-ref HEAD` |
| Starting commit | `933cbcb6` (`merge: T-veto-2 unconditional safety_veto stage 0`) | `git rev-parse HEAD` |
| Working tree | clean | `git status --porcelain` → empty |
| Baseline suite | **982 passed, 21 skipped, 0 failed** (69.5s) | `python -m pytest -q` (Python 3.12) |
| Python (test) | 3.12 (`Python312\python.exe`); system default is 3.14.6 | `python --version` + pytest path |
| Integration env | `SKYLIZE_TEST_DB_URL` / `SKYLIZE_TEST_REDIS_URL` unset → integration tests skip | env probe |
| `SKYLIZE_DECISION_ENGINE` | unset (default `inline`) | env probe |

### main vs feat/durable-governance — the "key unknown", RESOLVED

- `merge-base(main, durable)` = `0d36edc3` = **main's own HEAD**.
- main is an **ancestor** of durable-governance (`git merge-base --is-ancestor main durable` → exit 0).
- Counts: **main is 0 ahead / 43 behind** durable-governance (`git rev-list --count`).

**Verdict:** main is *strictly behind* `feat/durable-governance` by 43 commits. Everything on
main is already on durable-governance. There are **no** commits on main that durable lacks.
Phase 2 classification (a) ("already on main, absent from durable") is therefore the **empty set** —
if content is on main it is necessarily on durable (main ⊂ durable).

---

## Phase log

### Orientation — DONE
- Enumerated 29 local branches + 23 worktrees (`git branch`, `git worktree list`).
- Confirmed baseline above.
- Live M5 reference already spotted in ground-truth code: `pyproject.toml:220-222`
  ("post-launch M5 excision/rework — see the launch plan"). Remediation is expected via
  `fix/unsourced-m5-references` (Phase 1B); left untouched pending that merge.

### Phase 1A — ADR-0004 docs-only merge — DONE (commit `7f0882b7`)
- Real `--no-ff` merge of `docs/adr-0004-opa-production-arbiter`; resolved to **docs-only**.
- Branch's src edits (`bootstrap.py`, `app/decision_engine/engine.py`, `__init__.py`) are
  comment/docstring-only and were **discarded** — restored to HEAD verbatim (`git checkout HEAD --`,
  confirmed empty `git diff HEAD`). HEAD's stricter fail-closed guard preserved byte-for-byte.
- Added exactly one comment line — `bootstrap.py:221`: `# See ADR-0004: docs/architecture/adr/0004-opa-production-arbiter.md`
  inside the existing guard comment. Guard logic (`if settings.decision_engine != "inline": raise RuntimeError`) unchanged.
- Landed docs: `docs/architecture/adr/0004-opa-production-arbiter.md` (new) +
  `docs/04_decision_engine/decision_engine.md`, `guardrails.md`, `docs/_BUILD_LOG.md` (the 3 were unchanged
  on durable since the merge-base, so no durable content lost).
- Suite after: **982 / 21 / 0** (baseline held). `git branch --merged` now lists adr-0004.

### Phase 1B — remaining backlog branches — DONE (all 4 merged clean)
All merged one-at-a-time, full suite between each. `git branch --merged` confirms all 5 Phase-1 branches.

| Order | Branch | Commit | Result | Suite after |
|-------|--------|--------|--------|-------------|
| 1 | `docs/adr-0005-decision-engine-department-vocabulary` | `2cb665ad` | clean, doc-only (1 new file). Does NOT decide S1 (ADR is "Proposed"). | 982 / 21 / 0 |
| 2 | `fix/unsourced-m5-references` | `275d991e` | clean 3-way. Scrubs invented "M5 excision/launch plan" from 7 files (`pyproject.toml:219-220`, `qdrant_adapter.py:71`, 2 skip-reason strings, 3 narrative docs). Only skip-reason strings + comments touched; no test logic. | 982 / 21 / 0 |
| 3 | `feat/opa-infra-skeleton` | `31d2e8b6` | clean. 7 PLACEHOLDER fail-closed `.rego` (all `default allow := false`, none `allow:=true`) — S3-compliant. New OPA integration test is `integration`+`skipif(SKYLIZE_TEST_OPA_URL)` → skips. docker-compose `opa` service + `.env.example` OPA vars = repo-side config; engine stays `inline`. | **982 / 23 / 0** (skips 21→23: 2 new OPA integration tests, inert w/o server) |
| 4 | `feat/opa-railway-deploy` | `b940d654` | clean additive. `infra/opa/Dockerfile` + `railway.json` only. NO railway CLI run, no deploy. No secret values. | 982 / 23 / 0 |

**Phase 1 end state:** HEAD `b940d654` on `feat/durable-governance`; suite **982 / 23 / 0**.
Skip delta from stated baseline is +2, fully accounted (OPA integration tests). Passes/failures held.

### Phase 2 — main vs durable reconciliation — DONE

**main-vs-durable verdict (the key unknown):** main is **strictly behind** durable-governance by
**43 commits, 0 ahead**; main's HEAD `0d36edc3` IS the merge-base and is an ancestor of durable.
**No commit on main is absent from durable.** The "work split across two lines" hypothesis is false
for `main` — everything on main is already on durable. Class (a) ("on main, absent from durable") = ∅.

**Worktree safety:** the ONLY dirty worktree is this session's own main tree (2 untracked files = the
two report files). All 22 other worktrees are CLEAN — no external uncommitted work exists; no worktree
was touched.

**Method note:** classification used `git cherry` (patch-id) cross-checked with **per-file tip-vs-tip
diffs on each branch's signature files** — the 3-dot `A...B` stat was rejected as a supersession signal
because it stays large even after content is ported into durable.

| Branch | HEAD | uniq vs dur | on main? | on durable? | Class | Action |
|--------|------|-------------|----------|-------------|-------|--------|
| `feat/workflow-repository-postgres` | `c28af065` | 1 | no | **YES (identical)** — `dal/workflows.py`, `0010_workflow_run_steps.py`, `test_workflow_repository.py` all byte-identical on durable | (c) superseded | **report only** |
| `feat/tool-dedup-convergence` | `5e64e33e` | 1 | no | **YES (identical)** — `runtime/exec_fingerprint.py`, `authority.py`, 3 tests all byte-identical on durable | (c) superseded | **report only** |
| `chore/import-linter-orphan-check` | `0e0e919a` | 1 | no | **YES (superseded)** — `check_all_modules_importable.py` byte-identical on durable; durable's ci.yml already runs it + `find_orphan_modules.py` + `check_forbidden_imports.py`; branch ci.yml is *behind* durable | (c) superseded | **report only** |
| `fix/c2-temporal-docs` | `63d8f9f4` | 1 | no | no (cherry +) | (b) unmerged | **MERGED `ec04c263`** |
| `fix/h4-gemini-claims` | `78ff4095` | 1 | no | no (cherry +) | (b) unmerged | **MERGED `33d0b94c`** |
| `feat/grammar-gateway` | `3fa3d7cd` | 1 | no | no (cherry +) | (b) unmerged | **ABORTED — conflict (S8), queued** |
| `feat/tenant-isolation-rebase` | `0df89215` | 3 | no | **NO** — `memory/identity.py` etc. only on branch | (c)/S8 pair | **report only + queued** |
| `fix/knowledge-tenant-identity` | `3e1dca3f` | 2 | no | **NO** — same `identity.py` (byte-identical to the rebase branch) + unique website security work | (c)/S8 pair | **report only + queued** |
| `fix/c3-investor-status` | `2ae91bf4` | 1 | no | no | HELD | **DO NOT MERGE — investor wording, owner sign-off (report only)** |
| `release/console-m1` | `128ac0f3` | 9 | no | no | S7 | **leave untouched (report only)** |

**Tenant-isolation pair finding:** the tenant-isolation core (`src/skylize/memory/identity.py`) is
**NOT on durable** and is carried by BOTH `feat/tenant-isolation-rebase` and `fix/knowledge-tenant-identity`
with a **byte-identical** `identity.py`. The two branches diverge heavily elsewhere (different bases;
`knowledge-tenant-identity` additionally carries website console-auth/proxy + the n8n-proxy fail-closed
auth guard `25fd405c`). Picking/combining a canonical variant is an owner-level architecture decision (S8),
so neither was merged. Queued.

**Phase 2 end state:** HEAD `33d0b94c`; suite **982 / 23 / 0**.

### Phase 3 — autonomous backlog — IN PROGRESS

#### Phase 3A — land policy_inputs.md — **BLOCKED, QUEUED (not fabricated)**
`policy_inputs.md` could not be located **anywhere**: absent from durable HEAD, from every branch's
tree (`git ls-tree` scan of all 29 branches), from all history (`git log --all -- '**/policy_inputs*'`
empty), and from the filesystem (Glob `**/policy_inputs*` → none; worktrees + scratchpad included).
`docs/04_decision_engine/` holds 6 docs, none of them it.

Ground truth corroborates this is a *never-authored* doc, not a misplaced one — **ADR-0004 states it
four times**: "No `policy_inputs.md` exists in the repository … it does not yet exist and is not created
by this ADR" (`docs/architecture/adr/0004-opa-production-arbiter.md:27`), and that Rego authoring is
"gated on a `policy_inputs.md` input contract **that does not yet exist and must be authored**"
(`:43`, `:65`, `:67`).

Phase 3A's wording ("land it with its DRAFT banner intact") presupposes an existing draft to move into
place. None exists. Authoring one fresh would require inventing its section structure and the set of
`[OWNER-DECISION-REQUIRED]` items — owner-gated S2 territory and the exact M5 fabrication failure the
protocol forbids. **No file was created.** Queued for the owner (they must provide the draft or author
the contract). See `OWNER_DECISIONS_QUEUE_2026-07-19.md`.

The two facts Phase 3A wanted appended to policy_inputs.md §0.5 are **confirmed and preserved here** so
they are not lost, to be transcribed into §0.5 when the document is provided/authored:
  (i) the safety veto is now **unconditional** via the stage-0 `safety_veto_check` (verified in 3B below);
  (ii) the `SafetyVerdictOut → DecisionProposal.security_verdict` **producer bridge remains the last gap**
       and is blocked on S1 (department vocabulary).

#### Phase 3B — safety_veto stage-0 survived the merges — **VERIFIED** (HEAD `33d0b94c`)
- `evaluate()` at `src/skylize/app/decision_engine/evaluator.py:94` runs `safety_veto_check` as the
  **first substantive gate** (`:111-114`), before authority (stage 1 at `:116`). The comment at `:104-110`
  states it is "the PRIMARY gate … ahead of every other stage and regardless of whether a rival exists."
  The gate (`:173-193`) vetoes unconditionally on `verdict.reject`; absence / `reject=False` = "no signal",
  not an implicit reject. Only agent-contract resolution (a precondition, `:98-102`) precedes it.
- `test_security_veto_no_rival_still_blocks` (`tests/unit/test_decision_evaluator.py:234`) **PASSES**
  (1 passed in 0.11s). It drives the real `evaluate()` with a rejecting `security_verdict` and asserts
  `stages_completed == [STAGE_SECURITY]` — proving the stage short-circuits all others. Real path, not an
  impossible fixture (the evaluator/inline engine is wired). Caveat (queued): no live *producer* populates
  `security_verdict` end-to-end yet — that is the S1-blocked bridge — but the evaluator handling is live+tested.

#### Phase 3C — orphan-module import sweep — **CLEAN (193/193)** + 1 pre-existing CI-hygiene finding
- **Import sweep (the 3C deliverable): GREEN.** `scripts/check_all_modules_importable.py` (durable's own
  exhaustive importlib walk — `chore/import-linter-orphan-check` is superseded, Phase 2) imports every
  `.py` under `src/skylize` directly: **"OK: 193 modules import cleanly", exit 0.** No import failures.
  The historical blind spot (`activities.py` → `dal.ports.WorkflowRepository`) is closed — that content is
  on durable (Phase 2 confirmed `dal/workflows.py` byte-identical).
- **Finding (reported, NOT fixed — pre-existing):** `scripts/find_orphan_modules.py` exits **1** on current
  HEAD — a **stale-allowlist** condition, not a broken import or new orphan: `skylize.schemas.agents.safety`
  is listed in `scripts/orphan_modules.txt` but is now **reachable** via
  `src/skylize/app/decision_engine/events.py` (`from ...schemas.agents.safety import SafetyVerdictOut`),
  so the tool asks for its removal from the allowlist.
  - **Pre-existing, not caused by this session:** `git log 933cbcb6..HEAD` on both `orphan_modules.txt` and
    `schemas/agents/safety.py` is empty; no Phase 1/2 merge touched either file or `events.py`. This CI step
    was already red at the session's starting commit `933cbcb6`.
  - **Remediation (one line, tool-prescribed, deferred to owner/follow-up):** delete
    `skylize.schemas.agents.safety` from `scripts/orphan_modules.txt`. Zero-risk (tightens the contract).
    Left unfixed to honor 3C's "report, don't fix" and to avoid autonomously mutating a CI-enforcement file.
    NOTE: the CI job (`.github/workflows/ci.yml`) runs this step, so CI on the branch is red on it until removed —
    independent of the pytest suite, which is green.

#### Phase 3D — doc-vs-code drift sweep — 1 FIX committed (`1dc3cb30`) + drift reported

**FIXED (unambiguous factual error), commit `1dc3cb30`:**
- `docs/02_architecture/tech_stack.md` and `docs/architecture/03_agent_runtime.md` claimed
  *"pyproject.toml marks orchestrator.temporal.* as paused pending post-launch (M5) integration/rework."*
  That is **false on current HEAD**: `pyproject.toml:219-220` (scrubbed Phase 1B) now reads "no tracked
  removal or revival plan as of 2026-07-15" and carries no "M5"/launch-plan text; `orchestrator.temporal.*`
  is only a mypy override at `pyproject.toml:227`. Wording came in via `fix/c2-temporal-docs` (predated the
  scrub). Both docs now match ground truth and drop the unsourced "(M5)" label; technical fact preserved.
  Suite 982/23/0. **This is the only 3D fix — it is a factual error, not a phrasing preference.**

**Targeted checks — results:**
- *"Does any doc describe Temporal as 'not run in v1 / adoptable later'?"* — The primary offender
  (`tech_stack.md §5`) was already corrected by the merged `fix/c2-temporal-docs`. Ground truth: `temporalio>=1.7`
  is a hard dep (`pyproject.toml:66-68`), `src/skylize/app/orchestrator/temporal/` exists. **Two residual stale
  mentions remain, REPORTED not fixed** (both are historical records, not living docs):
  - `docs/_BUILD_LOG.md:32` — index line still summarizes tech_stack as "Temporal not separately run in v1".
    `_BUILD_LOG.md` is an append-only build log; rewriting it would falsify the record.
  - `docs/architecture/adr/0002-crewai-removal-langgraph-only.md:98-102` — an "Out of scope / follow-up" note
    saying tech_stack §5 "still reads 'Temporal not separately run in v1'". That follow-up has now been DONE
    (by `fix/c2-temporal-docs`), so the note is stale — but it is an ADR record; leaving it intact is correct.
- *"Any doc claiming OPA/policy enforcement is LIVE?"* — **None found.** Docs consistently describe OPA as the
  designated-but-unwired production arbiter (`decision_engine.md:13-21`, ADR-0004), and `.env.example`'s OPA
  block is explicitly labelled a fail-closed placeholder. No overstatement of OPA as running/enforcing.
- *"Any 'sole emitter' claims not reconciled to ADR-0004's per-environment flag-selected exclusivity?"* —
  The **docs are reconciled** (`decision_engine.md:13-14` "Per environment, exactly one … selected by
  `SKYLIZE_DECISION_ENGINE`"; `_BUILD_LOG.md:36` "per-environment, flag-selected sole emitter"). The claim
  survives **unqualified only in 3 source docstrings/comments**: `src/skylize/app/decision_engine/__init__.py:4`,
  `src/skylize/app/decision_engine/engine.py:4`, and the `bootstrap.py` "the ONLY emitter" comment (~`:228`).
  ADR-0004 (§Decision-2 `:37`, §Consequences `:57`) mandates correcting these — **but REPORTED, not fixed**,
  for two reasons: (1) ADR-0004:37 itself says the claim is *"not false, but incomplete"*, so it is a
  completeness improvement, not a factual error (3D fixes only factual errors); (2) the exact docstring
  correction was contained in `feat/adr-0004`'s src changes that Phase 1A deliberately discarded (docs-only,
  HEAD guard authoritative). **Recommendation:** apply the ADR-0004-mandated docstring correction as a clean,
  separate follow-up commit. Queued for owner awareness.
- *M5 residue full-repo* — after the `1dc3cb30` fix, remaining `\bM5\b` hits are ONLY in: (a) this session's
  own report/queue files (meta-references to the anti-M5 protocol); (b) three **historical** reports
  (`OVERNIGHT_REPORT.md`, `SESSION_A_REPORT.md`, `docs/testing/triage_report_2026-07-12.md`) that already carry
  explicit "CORRECTION (2026-07-15): 'M5' is unsourced" notes and are deliberately preserved point-in-time
  (`triage_report:81`: "left intact with this note appended rather than silently rewritten"). No live-doc M5
  references remain.

#### Phase 3E — test-suite health — DONE (report `docs/testing/test_suite_health_2026-07-19.md`, commit `35dab620`)
- Full run at HEAD: **982 passed / 23 skipped / 0 failed** (Python 3.12). **0 failures** (new or pre-existing).
- All 23 skips enumerated (`pytest -rs`) and classified: **21 environment-gated** (2 new OPA on
  `SKYLIZE_TEST_OPA_URL`; 19 pre-existing DB/Redis) + **2 documented dead/unwired-code**
  (`test_llm_agent_runner.py:61`, `test_memory_gateway.py:79`). Every skip intentional; none is test-rot.
- **Anti-M5 skip check PASS:** no skip reason references "M5"/launch-plan; the 2 dead-code reasons were
  scrubbed to "no tracked removal/rework plan" on `275d991e`. No test modified to pass/skip.

---

## Test counts at each checkpoint

| Checkpoint | passed | skipped | failed |
|------------|--------|---------|--------|
| Baseline (`933cbcb6`) | 982 | 21 | 0 |
| After 1A ADR-0004 (`7f0882b7`) | 982 | 21 | 0 |
| After 1B adr-0005 (`2cb665ad`) | 982 | 21 | 0 |
| After 1B m5-scrub (`275d991e`) | 982 | 21 | 0 |
| After 1B opa-infra (`31d2e8b6`) | 982 | **23** | 0 |
| After 1B opa-railway (`b940d654`) | 982 | 23 | 0 |
| After 2 c2-temporal (`ec04c263`) | 982 | 23 | 0 |
| After 2 h4-gemini (`33d0b94c`) | 982 | 23 | 0 |
| After 3D drift fix (`1dc3cb30`) | 982 | 23 | 0 |
| After 3E report (`35dab620`) | 982 | 23 | 0 |

Skip delta 21→23 = the 2 new OPA integration tests (skip-guarded on `SKYLIZE_TEST_OPA_URL`).
Passed (982) and failed (0) held at every checkpoint.

---

## Ending state

- **Branch:** `feat/durable-governance`
- **Ending commit (code/docs):** `35dab620`
- **Suite:** 982 passed / 23 skipped / 0 failed (Python 3.12).
- **Commits this session (8):** `7f0882b7` (1A ADR-0004 docs) · `2cb665ad` (1B adr-0005) ·
  `275d991e` (1B m5-scrub) · `31d2e8b6` (1B opa-infra) · `b940d654` (1B opa-railway) ·
  `ec04c263` (2 c2-temporal) · `33d0b94c` (2 h4-gemini) · `1dc3cb30` (3D drift fix) ·
  `35dab620` (3E health report). Plus this report + the owner queue, committed last.
- **Nothing pushed.** All local for morning review. No force-push, no reset, no branch/worktree deletion.

---

## FINAL SUMMARY

**Merged & green on `feat/durable-governance` (7 branches + 1 drift fix + 1 report):**
1A ADR-0004 (docs only, HEAD guard preserved verbatim), 1B ADR-0005 doc, 1B m5-scrub, 1B OPA-infra
skeleton (fail-closed placeholder rego, S3-safe), 1B OPA-railway config (no deploy), 2 c2-temporal-docs,
2 h4-gemini-docs. All full-suite-verified at 982 passed / 0 failed each.

**Still unmerged, and why:**
- `feat/grammar-gateway` — real conflict in `gateway.py`/`__init__.py` vs the evolved content-gated gateway
  (S8). Aborted + queued.
- `feat/tenant-isolation-rebase` + `fix/knowledge-tenant-identity` — the tenant-isolation core is genuinely
  unmerged but carried by a redundant pair (byte-identical `identity.py`, different bases); choosing the
  canonical variant is an owner architecture call (S8). Queued.
- `feat/workflow-repository-postgres`, `feat/tool-dedup-convergence`, `chore/import-linter-orphan-check` —
  SUPERSEDED (content already byte-identical on durable). No action, not deleted.
- `fix/c3-investor-status` — DO NOT MERGE (investor wording, owner sign-off). `release/console-m1` — S7, untouched.

**main vs durable-governance:** main is **strictly behind by 43 commits, 0 ahead**. Everything on main is
already on durable; nothing on main is missing from durable. No reconciliation merge was needed or possible.

**Single highest-priority owner decision blocking further progress:** **S1 — ADR-0005 department vocabulary.**
Until it is resolved (recommendation: accept Alternative A), the OPA engine can never be wired, real Rego
can't be authored, and the safety-verdict producer bridge stays blocked. See the owner queue.

**Tempted to decide but did NOT:** (1) which tenant-isolation branch is canonical; (2) how grammar-gateway
composes with the content gate; (3) authoring `policy_inputs.md` from scratch (it exists nowhere — I refused
to fabricate it); (4) correcting the ADR-0004-mandated "sole emitter" source docstrings (ADR calls the claim
"incomplete, not false" — reported, not fixed); (5) removing the stale `orphan_modules.txt` allowlist entry
that makes a CI step red (pre-existing; reported, not fixed).

---
---

# APPENDED SESSION — 2026-07-19 (later run)

> This is an **append**, not a rewrite. Everything above is a point-in-time record of an
> earlier run on the same date and is left intact. Where this run found an earlier claim to
> be stale or wrong, it is corrected **here**, with the proving `file:line`.

**Branch:** `feat/durable-governance`
**Starting commit:** `12ac6684` — clean tree except one untracked file (below)
**Ending commit:** `639c1cf0`
**Baseline suite at `12ac6684` (measured, not assumed):** 1051 passed, 23 skipped, 68.86s
**Final suite at `639c1cf0`:** 1068 passed, 28 skipped, 74.03s — 0 failures throughout

## Phase 0 — the two unknowns

**0A. Is `feat/opa-consumer-transport` merged into `feat/durable-governance`? — YES.**
`git branch --merged` lists it. Corroborated in code, not just by the branch pointer:
`SUBSCRIBED_STREAMS` survives only as a do-not-reintroduce note at
`src/skylize/decision_engine/constants.py:58-64`, and the worker module exists at
`src/skylize/decision_engine/worker.py`. No merge was needed.

**0B. Does `docs/04_decision_engine/policy_inputs.md` exist on this branch? — NO. Not tracked at all.**
`git ls-files docs/04_decision_engine/` returns six files and that is not one of them.
What exists is **`docs/04_decision_engine/policy_inputs (1).md`, UNTRACKED** — the `(1)` suffix
of a duplicated download. It was untracked at session start and was left untracked and unmodified.

Its banner reads `Status: DRAFT — AWAITING OWNER APPROVAL (Mr. Özkan)` and every section carries
`[RESEARCH-SUGGESTED]`, `[CODE-VERIFIED]` or `[OWNER-DECISION-REQUIRED]`. **No section reads `[APPROVED]`.**
**S2 is therefore still fully blocking** — no real Rego may be authored.

Two things the owner should know about that file:

1. It is **not in version control**, so it is one `git clean` from being lost, and no reviewer sees it in a diff.
2. Line 9 of it contains the phrase *"institutionalizes the M5 lesson"*. **`M5` has no tracked source in
   this repo.** It is the invented label this project has already been burned by, now embedded in the
   document meant to prevent exactly that. Not edited — it is an owner-facing draft awaiting approval —
   but flagged for correction before it is committed.

## What landed (4 commits, all local, nothing pushed)

| SHA | What | Suite after |
|---|---|---|
| `33a42a24` | Phase 1 — OPA HITL resume path + `governance` department | 1068 / 23 |
| `fe4f8f9d` | Phase 2B — docker-compose worker service, inert by profile | 1068 / 23 |
| `6cf271f2` | Delivery-semantics correction (docs/comments only) | 1068 / 23 |
| `639c1cf0` | Phase 2C — real-Redis integration coverage | 1068 / 28 |

### Phase 1 — HITL resume + governance department (`33a42a24`)

Landed as one change, as required. Hard exit gates, each with the test that holds it
(`tests/decision_engine/test_resume.py`, 14 tests):

- [x] **E2E, one `hitl_id` throughout** — `test_full_cycle_defer_then_resume_shares_one_hitl_id`
      drives proposal → defer → `hitl_queue` row + governance escalation → verdict → terminal
      `decision.approved`, asserting the same id in all four places.
- [x] **Redelivered approval does not double-terminate** — asserted at both layers.
- [x] **Resume does NOT run the six stages** — `test_resume_event_does_not_run_the_six_stages`
      asserts `pipeline_fn` is never called.
- [x] **`governance` drives BOTH subscription and AUTHORITY** — asserted in
      `tests/decision_engine/test_department_vocabulary.py`.
- [x] **Flag off → zero behaviour change** — the inline engine was not touched by this commit
      (`git diff --name-only` showed no file under `app/decision_engine/`).
- [x] **Full suite / mypy / ruff / forbidden-imports / module-importability / orphans** — all clean.

Two design findings worth the owner's attention, both verified at HEAD:

1. **`hitl_id` really is minted once, deterministically** — `pipeline.py:74` `uuid5(_HITL_NS, decision_id)`,
   minted at `orchestrator.py:72-76` upstream of both writers. Verified before relying on it, per Rule 5.
   The earlier ADR-0005 list of a double-mint is stale; that fix is merged.

2. **`publisher.publish_outcome` could not be reused for a resume, and this was not previously recorded.**
   Its CTE gates the outbox INSERT on the `decisions` row being *newly* inserted
   (`ON CONFLICT (decision_id) DO NOTHING`, `publisher.py:279-286`). A resume targets a row that already
   exists from the deferral, so that INSERT always conflicts and **the terminal event would never be
   enqueued.** `resume.py` therefore mirrors the shape inverted — UPDATE where the publisher INSERTs,
   outbox row still gated on the transition actually happening.
   No storage was invented: `hitl_queue.status` / `verdict_by` / `verdict_json` / `verdict_at` and
   `decisions.resolved_at` all exist in `migrations/versions/0001_initial_schema.py:170-220`, and the
   `status = 'pending'` guard is what makes a redelivery a no-op.

`governance` is now the one department the engine **subscribes to but is never authorized to decide for**.
`SUBSCRIBED_DEPARTMENTS` and `ALLOWED_DEPARTMENTS` are consequently no longer identical — both still
project from the single ADR-0005 table, and a test asserts the difference is exactly the resume-only
departments. The pre-existing `assert SUBSCRIBED_DEPARTMENTS is ALLOWED_DEPARTMENTS` was replaced by that
stronger invariant; it was changed because the invariant genuinely changed, not to make anything pass.

## Phase 2

**2A — import-linter: QUEUED (S9), not fixed.** See queue item **Q1**.

Two corrections to the brief's own premise, both verified:

- **The brief says "violating three contracts." It violates ONE.** `lint-imports` reports
  `Contracts: 4 kept, 1 broken` — the single broken contract is
  *"Application logic contains no SQL (depends on dal ports only)"* (`pyproject.toml:171`), via
  **three forbidden-module clauses** (`asyncpg`, `skylize.dal.connection`, `skylize.dal.repositories`).
  Clauses were counted as contracts.
- **`python -m importlinter.cli lint-imports` is a silent no-op** — zero output, exit **0**. I ran it
  first and nearly recorded "lint-imports is green." The console script `lint-imports` exits **1**.
  CI is not fooled (`.github/workflows/ci.yml:25` runs the console script). Anyone who has "verified
  green" with the `-m` form verified nothing.

It is red, it is pre-existing, and **my changes do not touch it** — `skylize.decision_engine` appears
nowhere in the violation output. The sole violator is
`src/skylize/app/orchestrator/temporal/worker.py:32,34`.

**2B — docker-compose worker entry: LANDED (`fe4f8f9d`).** Inert via `profiles: ["opa-engine"]`
(verified: absent from the default service set, present only under the profile), not via the
startup interlock — leaning on the interlock would boot-crash a container on every `up`, which is
indistinguishable from a real outage. Repo-side config only; nothing deployed, no flag flipped.

It also corrects a name mismatch that would have broken a naively-copied entry:
`DecisionEngineSettings.database_url` reads **`SKYLIZE_DATABASE_URL`** (`decision_engine/config.py:30`,
no default), while every existing compose service supplies `SKYLIZE_DB_URL` / `SKYLIZE_DB_APP_URL`,
which belong to the separate `Settings` class (`config.py:36-37`). The names do not overlap; the worker
would have died on pydantic validation. Same for the two required Langfuse keys
(`decision_engine/config.py:26-27`), passed by name only.

**2C — real-Redis integration coverage: LANDED (`639c1cf0`).** Five tests in
`tests/integration/test_decision_engine_consumer_redis.py`, gated on `SKYLIZE_TEST_REDIS_URL` via the
existing `requires_redis` mark (`tests/integration/conftest.py:34`); they skip without infra (verified).

**Stated plainly, as instructed:** OPA remains mocked at the `pipeline_fn` seam, so the fail-closed
paths (timeout, unreachable, non-200) have **still never met a real OPA server**. Worse than that —
see correction C4 below, the one test that *would* exercise a live server cannot pass.

**Honesty caveat, also written into the module docstring:** this file has **never been executed against
a real Redis.** This machine has no Redis listener, no Docker and no `redis-server` binary. Every
assertion is verified by reading the adapter only. CI's integration job supplies Redis and will be the
first real run — treat a failure there as this file being wrong until proven otherwise.

**2D — XAUTOCLAIM reclaim: QUEUED (S9), not implemented.** See queue item **Q2**. The audit verdict was
STOP-AND-QUEUE and an adversarial verifier upheld it. The headline is that the gap is much wider than the
`consumer.py` note described, which produced the one substantive code correction of this session:

### `6cf271f2` — the at-least-once claim was false, in eight places

**`RedisEventBus.consume` reads `{stream: ">"}` — new messages only (`redis_adapter.py:55`) — and the
adapter issues no XAUTOCLAIM, XCLAIM or XPENDING anywhere.** A message left un-acked by a failing
handler is therefore never re-read: not by a peer worker, not by the same worker after a restart. It
stays in the PEL permanently.

Consequences that were documented backwards:

- `router.py`'s `# else: no ack → redelivery` **strands** the message rather than retrying it.
- `_attempts[event_id]` can never exceed 1, so the `>= dlq_after` branch is **unreachable** for handler
  failures, and `dlq_after_retries` (default 5, `config.py:88`) and `redis_max_retries`
  (`decision_engine/config.py:23`) are **dead config**.

Effective semantics are **at-MOST-once for failures**, not at-least-once.

This is the "coded but never executed" failure class in its purest form: **both tests covering the DLQ
path are green while proving nothing about reachability**, because each manufactures the redelivery the
bus cannot produce — `tests/integration/test_event_router.py:57` calls `router._dispatch` directly three
times, bypassing the bus entirely, and `tests/decision_engine/test_consumer.py:324` republishes the
event under a comment asserting that is *"what an unacked Redis PEL entry becomes"*, which is exactly
what does not happen.

Corrected in `router.py`, `redis_adapter.py`, `bus.py`, `decision_engine/consumer.py`,
`decision_engine/orchestrator.py`, `app/decision_engine/engine.py`, and both misleading test comments.
**No logic was changed and no test was modified to pass or skip** — the two DLQ tests still pass and are
worth keeping; their counting logic is correct for when redelivery exists. Two of the eight corrected
claims were introduced by **this session's own** Phase 1 commit, which inherited the false framing.

The gap is now also **executable**: the fifth new integration test asserts the current behaviour (failed
handler → stranded in PEL, no redelivery, never reaches DLQ) and is written to **fail loudly when reclaim
is implemented**, at which point it should be rewritten to assert retry-then-DLQ rather than deleted.

## Phase 3

**3A — orphan/import sweep: CLEAN.** `python scripts/check_all_modules_importable.py` →
`OK: 196 modules under src/skylize import cleanly.`, exit 0 (196, up from 195, because `resume.py` is
new and is covered — the script has no allowlist). `python scripts/find_orphan_modules.py` →
`OK: no new orphan modules (14 known, allowlisted).`, exit 0.
**Correction to the earlier section of this file:** its lines 145-155 record this gate as exit 1 with a
stale `skylize.schemas.agents.safety` entry. That was true when written and is **no longer true** —
commit `25ffd10b` removed the stale allowlist entry. Both gates are green as of `639c1cf0`.

**3B — doc-vs-code drift.** The audit surfaced candidates; the ones acted on were the code-comment
delivery-semantics errors above, which are the ones that were actively propagating into new code.
Point-in-time reports were **not** rewritten — this appended section is the dated correction, per the
git-discipline rule.

**3C — test-suite health.** Full run at `639c1cf0`: **1068 passed, 28 skipped, 0 failed.**

- 23 skips are the pre-existing baseline (unchanged in count and reason from `12ac6684`).
- 5 skips are new and all from this session's `test_decision_engine_consumer_redis.py`, reason
  `SKYLIZE_TEST_REDIS_URL not set` — the established convention.
- **No skip reason references an unsourced label.** No test was modified to pass or skip.

**3D — flag-flip readiness: see the next section.**

## FLAG-FLIP READINESS STATEMENT

Verified against code at `639c1cf0`. What remains before `SKYLIZE_DECISION_ENGINE=opa` in staging:

| # | Blocker | Proof | Gate |
|---|---|---|---|
| B1 | **Rego cannot approve anything.** The whole bundle is a fail-closed placeholder; the engine would correctly REJECT every proposal. | `policy/skylize/decision/decision.rego:14` `default allow := false` plus its comment "Nothing below can flip this to true"; `grep "allow := true"` across `policy/` hits **only comments saying no rule sets it**, in all 6 class files | **OWNER** (S2 — blocked until `policy_inputs.md` sections read `[APPROVED]`; that file is not even tracked) |
| B2 | **No `policy_version` rule in the bundle.** | `grep -rn "policy_version" policy/` returns no matches; `opa_client.py` warns only | OWNER (rides with B1) |
| B3 | **No OPA server for staging.** A local one exists in compose; staging has none. | `infra/docker-compose.yml:42-52` (local only); `grep -rni "opa" infra/terraform/` returns no matches | **OWNER** (S4) |
| B4 | **The flag and org-ids are set nowhere in deploy config.** A worker deployed today `RuntimeError`s on start. | `grep -rn "SKYLIZE_DECISION_ENGINE" .github/ infra/` finds nothing outside the compose entry added this session; `consumer.py:129-133` raises on empty `org_ids` | Engineer, blocked behind B3 |
| B5 | **Nothing deploys the worker to staging.** Compose now describes it; the ECS pipeline still ships one container. | `.github/workflows/deploy-staging.yml:17` `CONTAINER_NAME: api`; root `Dockerfile` CMD is uvicorn-only | Engineer, blocked behind B3 |
| B6 | **The one live-OPA test cannot pass.** See correction C4. | `tests/decision_engine/test_opa_client_integration.py:59,76` | Engineer — **fixable now** |
| B7 | **No redelivery.** Not strictly flag-blocking, but a failed proposal is silently stranded under either engine. | `redis_adapter.py:55` | OWNER (Q2 — design decision) |

**Verified NOT blockers** (assumptions worth retiring):

- HITL resume path — **landed this session** (`33a42a24`, `resume.py` + `consumer._handle_resume`).
  The `worker.py` docstring listing it as a blocker was corrected in the same commit.
- Migrations — all four tables the engine writes exist: `decisions` and `hitl_queue`
  (`0001_initial_schema.py:170,203`), `decision_outbox` (`0009_add_outbox_table.py:48`),
  `decision_processed_events` (`0011_decision_engine_stores.py:54`).
- Policy path/package agreement — `config.py:15` `"skylize/decision"` matches
  `policy/skylize/decision/decision.rego:9` `package skylize.decision`.
- The two-engine interlock — works in both directions (`worker.py:72-77`, `config.py:103`).
- Outbox relay — wired and tested; nothing else relays `decision_outbox`, so no double-publish.

## CORRECTIONS — assumptions in the brief or in earlier reports that were WRONG

**C1. Staging is AWS ECS, not Railway.** The brief frames S4 and the readiness question around
"Railway staging" / "Railway environment creation." `.github/workflows/deploy-staging.yml:13-17` deploys
to `ECS_CLUSTER: skylize-staging` on AWS, and the only tracked Railway files are `infra/opa/railway.json`
and `website/railway.json` (`git ls-files | grep -i railway`). This matches the locked stack's own
"Railway (staging), AWS ECS Fargate (prod target)" only if staging has already moved to ECS ahead of that
description. **Which platform staging actually is, is an owner question** — see queue item **Q4**. Nothing
was deployed either way.

**C2. "lint-imports violates three contracts" — it violates one contract via three clauses.**
`Contracts: 4 kept, 1 broken`. Detail in Phase 2A above.

**C3. `policy_inputs.md` does not exist as a tracked file** — only an untracked `policy_inputs (1).md`
exists, in DRAFT with no `[APPROVED]` section, and it contains the unsourced label `M5`. Detail in Phase 0B.

**C4. The live-OPA integration test has never run and cannot pass in its current form.**
`tests/decision_engine/test_opa_client_integration.py:59` and `:76` do
`allow, deny_reasons = await client.evaluate(...)`, but `evaluate` returns an `OPAResult` with four fields
(`opa_client.py:82`, `models.py:41-47`). Unpacking raises `ValueError: too many values to unpack
(expected 2)` — reproduced independently by two agents against the real class. It is gated on
`SKYLIZE_TEST_OPA_URL`, which is set in **no** CI config, so it has always skipped and the breakage has
never surfaced. **Not fixed here**, because Phase 3C forbids modifying tests; it is an unambiguous,
cheap engineer-fixable defect and is listed as B6. The practical consequence: the first person to stand
up an OPA server will have their smoke test explode for a reason unrelated to their server.

**C5. The `consumer.py` "KNOWN GAP" note understated the problem.** It described a missing reclaim of a
*dead worker's* PEL. The actual gap is that redelivery does not exist at all — a worker does not even
recover its own PEL on restart. Corrected in `6cf271f2`.

## Things I was tempted to decide and did NOT

1. **The import-linter layering** (move the entrypoint out of `skylize.app` vs amend the contract). Both
   are defensible and there is a real doc-vs-code gap in the config's own comment. → Q1.
2. **Implementing XAUTOCLAIM.** Contained-looking, genuinely not: it would change the *inline* engine's
   emission behaviour the moment it landed, and needs a field the shared port does not have. → Q2.
3. **Authoring any Rego content.** S2 holds; `policy_inputs.md` has no approved section.
4. **Fixing the broken live-OPA test.** Cheap and obviously right, but Phase 3C says do not modify tests. → B6.
5. **Editing `policy_inputs (1).md`** to remove the unsourced `M5` reference. It is an owner-facing draft
   awaiting his approval; flagged instead.
6. **Committing `policy_inputs (1).md` to git.** Tracking it is an owner act, and the filename alone
   implies a duplicate whose canonical name he should choose. → Q3.

## Git discipline

Four commits, one logical unit each, no merge bundled with unrelated edits. Full suite run between every
one; zero new failures at any checkpoint. **Nothing pushed — everything is local for morning review.** No
force-push, no reset, no branch deletion, no worktree touched. The working tree at end of session is clean
except the same untracked `docs/04_decision_engine/policy_inputs (1).md` it started with.
