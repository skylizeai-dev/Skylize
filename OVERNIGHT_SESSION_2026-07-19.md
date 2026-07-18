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
