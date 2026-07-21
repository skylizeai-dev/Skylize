# Overnight Session — 2026-07-21

**Branch:** `feat/durable-governance` throughout. Nothing pushed. No branch or worktree
deleted. No deploy, no environment created, no secret operation, no flag flipped.

| | |
|---|---|
| Starting commit | `652393fd` |
| Ending commit | `88eedeed` |
| Measured baseline | **1068 passed, 28 skipped, 0 failed** |
| Final | **1080 passed, 31 skipped, 0 failed** |

The baseline was measured in this session, not taken from any document. Every "no new
failures" claim below is against that measured number.

Every claim in this report cites a file:line or a commit SHA. Where a reference could not
be confirmed, the report says so in those words.

---

## Phase 0 — Orientation

Working tree was clean at start except one untracked file
(`docs/04_decision_engine/policy_inputs (1).md`), which is itself finding **0A**.
27 worktrees exist; **all 27 were clean** and none was touched.

### 0A — `policy_inputs.md` is NOT tracked

`git ls-files docs/04_decision_engine` returns six files and does not include it. What
exists on disk is `docs/04_decision_engine/policy_inputs (1).md` — 23,775 bytes, modified
2026-07-19 16:13, untracked, one `git clean -fd` from deletion.

**This is a change since the 2026-07-19 queue**, which recorded that the file "does not
exist anywhere". It now exists; the owner evidently supplied it. It was **not renamed and
not authored** — the session brief forbids rename-guessing it, so it is queued as **D1**.

Its banner (line 3) reads `Status: DRAFT — AWAITING OWNER APPROVAL`. **No section reads
`[APPROVED]`.** Sections §0.1, §0.2, §0.3, §0.4, §0.5 and §0.7 are all
`[OWNER-DECISION-REQUIRED]`. Hard stops **S1 and S2 are therefore fully in force**: no
Rego content was authored and no owner value was filled in.

### 0B — Reclaim confirmed absent at baseline, then landed

At `652393fd`, searching all of `src/` for `xautoclaim|xpending|xclaim` returned **zero
matches**, and `redis_adapter.py:64-65` read `{stream: ">"}` only. The adapter's own
docstring (`redis_adapter.py:8-16`) admitted "NOT at-least-once".

The fix existed on an **unmerged** branch, `fix/bus-redelivery-reclaim` — exactly one
commit ahead of HEAD. See Phase 2.

### 0C — `hitl_id` is minted ONCE (the ADR's blocker list is stale)

Verified at `orchestrator.py:79-83`: minted once via `hitl_id_for(result.decision_id)`
(deterministic uuid5, `pipeline.py:67-75`), then passed to the publisher
(`orchestrator.py:85`) and the hitl_writer (`orchestrator.py:101`). **One mint, two
consumers.** Any document still listing "hitl_id is minted twice" as an open blocker is
out of date; the fix is merged.

### 0D — Both import checks, run correctly

| Check | Result |
|---|---|
| `python scripts/check_all_modules_importable.py` | **exit 0** — 196 modules |
| `lint-imports` (console script) | **exit 1 — FAILS** |

`lint-imports` breaks the contract *"Application logic contains no SQL"*
(`pyproject.toml:170-174`) through `skylize.app.orchestrator.temporal.worker` importing
`skylize.bootstrap` and `skylize.dal.workflows`. **CI runs this exact command**
(`.github/workflows/ci.yml:25`), so **CI is red on this branch**. It is **not** red on
`main` — that module does not exist there (`git cat-file -e main:…` fails); it arrived in
`1ef9281d` (2026-07-15). Queued as **D4**; it is a layering decision, not a stale
allowlist entry.

> The brief's warning held: `python -m importlinter.cli lint-imports` exits 0 silently.
> Every result here is from the console script.

---

## Phase 1 — Mechanical fixes

### 1A — `85e7990f` — OPAResult unpacking

Confirmed still broken on HEAD before touching it: both tests unpacked
`client.evaluate(...)` into two names, but `evaluate` returns an `OPAResult` pydantic model
with four fields (`models.py:41-47`). Unpacking it raises TypeError. The tests never ran —
`pytestmark` skips the module unless `SKYLIZE_TEST_OPA_URL` is set
(`test_opa_client_integration.py:29-36`) — so the breakage was invisible, not absent.
Switched to attribute access. Still integration-marked and skip-guarded.

### 1B — `fedc4b56` — sole-emitter wording

Both engine docstrings already carried the corrected per-environment wording
(`app/decision_engine/engine.py:4-5`, `decision_engine/worker.py:21-24`), as did
`docs/_BUILD_LOG.md:36`. The **one** surface still making an unqualified claim was the
`decision_engine` setting comment in `src/skylize/config.py`. Corrected to the
per-environment, flag-selected wording, citing ADR-0004 §Decision 2
(`docs/architecture/adr/0004-opa-production-arbiter.md:37`). Comment-only.

### 1C — No commit needed

The dormant worker compose entry **already exists and is already correctly gated**:
`infra/docker-compose.yml:112-113`, `profiles: ["opa-engine"]`, so `docker compose up`
never starts it. It is not in `deploy-staging.yml`. Nothing to fix; the brief's assumption
that this might be missing was wrong.

---

## Phase 2 — Message delivery: **at-least-once now holds**

`897d75ce` merges `fix/bus-redelivery-reclaim`. The work was already authored on that
branch; this session verified it against the exit gates and landed it.

**What it does.** `RedisEventBus._reclaim` issues `XAUTOCLAIM` over the group's PEL before
each `">"` read, driven by `reclaim_min_idle_ms`. `InMemoryEventBus` gained a real pending
list so it redelivers too. `EventRouter._attempts` now actually increments across
redeliveries, so the retry budget exhausts into the DLQ instead of stranding.

**The S10 gate was respected, and I checked this specifically rather than trusting the
commit message.** The change does **not** add a delivery-count field to the shared
`EventBus` port; `bus.py:71-81` documents that as a real but deliberately-unmade port
change (Kafka has no native equivalent). And it does **not** invent a first-start backlog
policy: reclaim is **rate-bounded, not age-bounded** — `count=reclaim_batch` per pass, with
an explicit rejection of an age ceiling on the grounds that discarding old PEL entries
would silently abandon decisions. Both limits are documented in
`redis_adapter.py:8-31`. The residual limitation is stated honestly in `router.py`:
`_attempts` is process-local, so a message that kills every worker retries afresh per
process rather than reaching the DLQ.

**Answering the brief's question plainly:** at-least-once is now **TRUE for the in-memory
bus, proven by tests that ran**, and **TRUE-by-construction for Redis, not proven by
execution.** First-start backlog behaviour in a real deployment: a large accumulated PEL
drains steadily at `reclaim_batch` entries per pass rather than arriving as one flood,
and nothing is discarded by age.

### Exit gates — honest status

| Gate | Status |
|---|---|
| Un-acked message redelivered after idle timeout | Test exists (`test_redis_bus.py:105`) — **never executed** |
| `_attempts` increments; budget exhausts into DLQ | **Executed and passing** on the in-memory bus (`test_event_router.py:47`); real-Redis version (`test_redis_bus.py:198`) never executed |
| Redelivery does not double-process, both engines | **Executed and passing** (`test_event_router.py:88`, `test_consumer.py:364`) |
| Flag off → inline behaviour verified | **Executed** — full suite green with the flag at its `"inline"` default |
| No new failures vs measured baseline | **Met** — 1074/31/0 at the merge |
| ruff / mypy / forbidden-imports | **Clean** |
| Verified with the console script `lint-imports` | Yes — and it fails, pre-existing (D4) |

**The four real-Redis tests could not run here.** Docker is not installed
(`docker` is not a recognised command) and nothing listens on `127.0.0.1:6379`; no
`SKYLIZE_TEST_*` variable is set. This is a "coded but not executed" gap and is recorded
as such in `docs/testing/test_suite_health_2026-07-21.md`.

---

## Phase 3 — Safety-verdict producer bridge: **NOT BUILT. Stopped and queued.**

The re-audit did not find the bridge buildable. It found something worse, and this is the
most important paragraph in this report.

### The brief's premise was wrong, in the same way this project has been burned before

The brief states the safety veto is "fully built ... unconditional stage-0
`safety_veto_check`". **That is true of the inline engine and false of the OPA engine** —
the two same-named packages the anti-M5 protocol explicitly warns about.

- `src/skylize/app/decision_engine/` (**inline**) — stage-0 veto at `evaluator.py:104-114`,
  running ahead of authority and terminating on a rejecting verdict. Carrier at
  `events.py:59`, mapper at `events.py:77-90`, field at `events.py:118`.
- `src/skylize/decision_engine/` (**OPA — the engine the flag activates**) — searching the
  entire package for `security_verdict`, `safety_veto` or `SafetyVerdict` returns **zero
  matches**. Its pipeline runs six stages starting at `_stage_authority`
  (`pipeline.py:177-187`). **There is no stage 0.**

**Flipping the flag today would silently delete the absolute safety veto from the running
system.** Queued as **D6**.

### The bridge itself is also blocked, independently

- **No runtime producer of `SafetyVerdictOut` exists.** In `src/` it appears only as a
  class definition, an import, and four `output_schema` strings. The only assignment to
  `security_verdict=` in the repository is in a test
  (`tests/unit/test_decision_evaluator.py:68`).
- **The vocabulary has no slot for a verdict.** ADR-0005 Alt A did land — the explicit
  table is at `constants.py:28-44` and includes `governance` (`:43`) — but its four event
  types are `creative.review_requested`, `sales.campaign_proposed`,
  `sales.budget_reallocation_proposed`, `governance.human_approval_received`. None carries
  a safety verdict. Adding one is, per that file's own comment at `constants.py:25-27`,
  "an explicit, reviewable governance decision".
- **Embedding it on business events is a schema change**, not an enrichment: the proposal
  payloads and `BaseEvent` are `extra="forbid"`.
- **The synchronous inline path stays forbidden**, per the brief.

So the bridge would have required inventing a new event type — a vocabulary decision — and
it was not built. Queued as **D5**.

---

## Phase 4 — Live-OPA readiness without a live server

### 4A — `bf3d8dcb` — a real fail-closed hole, found and closed

Coverage before: non-200 (`:66`), timeout (`:81`), connect-error retry (`:97`), missing
`allow` (`:199`), `require_human` (`:215`, `:233`), `policy_version` absent on allow
(`:267`), non-dict result (`:287`) — all in `test_opa_client.py`.

**Two genuine gaps, both now closed:**

1. **The malformed-body branch had no test at all.** Every existing test stubs
   `resp.json` with a MagicMock, so httpx never decodes and `except ValueError` was
   unreachable by any of them. The new tests drive real bytes through
   `httpx.MockTransport`.
2. **A 200 whose top-level JSON is not an object escaped as a bare `AttributeError`** —
   a crash, not a denial, directly contradicting the class's fail-closed contract
   (`opa_client.py:42-45`). I verified this empirically against a real transport before
   changing anything: `[1,2,3]`, `"allowed"` and `42` each raised
   `AttributeError: '…' object has no attribute 'get'`. Now guarded at `opa_client.py:144`.
   The pre-existing isinstance check guards the `result` *value*; this guards the envelope.

No fail-closed behaviour was weakened to make anything pass.

### 4B — the brief's assumption was right about the fix, wrong about its location

The brief says "OPAClient must POST payload via `model_dump(mode="json")` (a prior fix)".
**There is no `model_dump` in `opa_client.py`.** The protection lives at the **producer**:
`consumer.py:239`, whose comment names this exact hazard ("httpx's `json=` uses the stdlib
encoder, which raises on a UUID").

That matters because it makes the defect **unreachable**, and I checked rather than
assumed: there is exactly **one** `DecisionContext` construction site in all of `src/`
(`consumer.py:229`). A guard test already exists and drives a real event through the bus
(`test_consumer.py:207`). A defensive fail-closed guard was still added at
`opa_client.py:100` — unreachable today is not a reason for the contract to be false.

### 4C — `0b8b6aa6` — bring-up checklist, plus three stale citations corrected

`docs/08_operations/opa_staging_bring_up.md` documents the manual steps, every one citing
its source file. **Nothing in it was executed.**

While verifying my own citations I found I had copied a stale one out of the compose file,
which led to finding two more. All three corrected:

- `consumer.py:129-133` → **`:160-163`** (129 is `subscribe`; the empty-org-ids guard is 160).
- `worker.py:64-77` → **`:78-83`** (64 is blank, 77 closes a docstring).
- `config.py:109` → **`:113`**.

This is the drift the checklist exists to prevent, found in the act of writing it.

---

## Phase 5 — Sweeps

### 5A — gates

`check_all_modules_importable` 0 · `find_orphan_modules` 0 (14 known, allowlisted) ·
`check_forbidden_imports` 0 · `ruff` 0 · `mypy src` 0 (196 files) · **`lint-imports` 1
(D4)**. No allowlist entry was edited; the failure needs a layering decision.

### 5B — `88eedeed` — four live docs corrected

Each asserted a control that is not in the build. Corrections are status notes beside the
target-state text, not rewrites of intent:

- `docs/07_security/permissions.md` — listed "OPA guardrail" as a live enforcement point in
  both the authorization formula and the defense-in-depth table. The wired engine never
  contacts OPA: its policy stage is hard-coded Python, self-described as "the MVP stand-in
  for OPA Rego" (`app/decision_engine/evaluator.py:216`).
- `docs/04_decision_engine/guardrails.md` §6 — claimed Rego unit tests run in CI and block
  the build. No `*_test.rego` exists and `grep -rni 'opa\|rego' .github/` returns **zero**
  matches; `ci.yml:20-35` has no policy step.
- `docs/09_development/deployment_strategy.md` and `coding_standards.md` — same false CI
  gate claim.

**Deliberately not changed:** `guardrails.md` §5's response contract. That is the spec real
Rego must meet; placeholders not meeting it is expected, not drift.

**Unsourced-label sweep:** the `M5` / "launch plan" / "excision" tokens survive in `docs/`
only inside dated point-in-time reports that already carry their own corrections, plus the
untracked draft. **No live document makes a fresh unsourced claim**, so nothing was
rewritten. (The untracked draft introduces its own `Faz 0/1/2` labels; since the file is
untracked and owner-supplied, that is noted, not edited.)

### 5C — `docs/testing/test_suite_health_2026-07-21.md`

All 31 skips classified: 29 infrastructure-gated (Redis/Postgres/OPA absent), 2 dead-code.
**No skip reason cites an unsourced label** — both dead-code skips say "no tracked …
plan". No test was modified to pass or skip.

---

## 5D — FLAG-FLIP READINESS STATEMENT

What remains before `SKYLIZE_DECISION_ENGINE` can be set to `"opa"` in staging. Verified
against code at `88eedeed`, not against documents.

| # | Blocker | Evidence |
|---|---|---|
| 1 | **Owner-approved policy inputs.** No section is `[APPROVED]`; the file is not even tracked. | `policy_inputs (1).md:3,19`; `git ls-files` — **absent**. D1, D2 |
| 2 | **Real Rego content.** All 7 files are `default allow := false` with no `allow := true` and no conditional allow rule anywhere. | `authority.rego:13`, `brand_legal.rego:13`, `data_access.rego:13`, `decision.rego:14`, `external_action.rego:13`, `security_veto.rego:14`, `spend.rego:13`. D3 |
| 3 | **A live OPA server.** Never contacted by this codebase. Railway image exists; **no ECS/Terraform resource provisions one.** | `infra/opa/Dockerfile`, `infra/opa/railway.json` exist; `infra/terraform/staging/modules/ecs/main.tf:62` declares a single `api` container |
| 4 | **Flag + org_ids in a deploy config — verified absent.** Only occurrence repo-wide is the profiled local compose service. | `infra/docker-compose.yml:120,124`, gated at `:113`. Searched all `*.yml/*.yaml/*.json/*.tf/*.ps1/*.sh/*.toml/*.example/Dockerfile*`: no other hit |
| 5 | **NEW — the OPA engine has no safety veto.** Flipping would silently delete the absolute veto. | zero matches for `security_verdict\|safety_veto\|SafetyVerdict` in `src/skylize/decision_engine/`; `pipeline.py:177-187` starts at authority. D6 |
| 6 | **NEW — the spend amount never reaches OPA.** | `consumer.py:229-240` + `opa_client.py:61-65`; locked by `test_opa_client.py::test_real_event_loses_its_spend_fields_before_reaching_opa`. D7 |
| 7 | **NEW — CI is red on this branch.** It cannot merge to `main` as-is. | `lint-imports` exit 1; `ci.yml:25`. D4 |
| 8 | **The safety-verdict bridge is unbuildable within the tracked vocabulary.** | `constants.py:28-44`; only `security_verdict=` assignment repo-wide is `tests/unit/test_decision_evaluator.py:68`. D5 |

**The brief expected four remaining blockers. There are eight.** Items 5, 6 and 7 were
found this session and none appears in any prior document. Item 5 is the one that matters
most: it means the flag flip is not merely "not ready" — done today it would **remove a
governance control the product claims to have**.

---

## Assumptions in the brief that turned out WRONG

Recorded prominently, per the brief's own instruction.

1. **"The safety veto is fully built."** True of the inline engine only. The OPA engine has
   no veto stage at all. **This is the same two-same-named-packages trap the anti-M5
   protocol warns about, and the brief itself fell into it.**
2. **"A prior fix made OPAClient POST payload via `model_dump(mode="json")`."** No such code
   is in `opa_client.py`. The protection is at the producer, `consumer.py:239`.
3. **"Phase 2 is work to do."** It was already authored on `fix/bus-redelivery-reclaim`,
   unmerged. The work was to verify and land it, not write it.
4. **"1C: confirm the compose entry exists and is gated."** Already existed, already
   correctly profiled (`infra/docker-compose.yml:112-113`). No change needed.
5. **"Fail-closed paths are correct by construction."** Two were not: a non-object response
   body crashed instead of denying, and the malformed-body branch had no test.
6. **The 2026-07-19 queue's S2** ("policy_inputs.md does not exist anywhere") is now stale —
   the owner supplied it; it is untracked.
7. **The 2026-07-19 queue's S1** (ADR-0005 vocabulary) is **resolved** — merged, verified at
   `constants.py:28-44`.

## What I was tempted to decide but did NOT

- Renaming `policy_inputs (1).md`. Obvious, two commands, and still the owner's to make.
- Filling in even one `[OWNER-DECISION-REQUIRED]` value — §0.5's security stance is a single
  line whose recommended answer matches what the code already does. Still not mine.
- Widening `SAFE_PAYLOAD_KEYS` to include `proposed_budget_minor_units`. That silently
  decides the amount/currency model (D7).
- Adding a stage-0 veto to the OPA pipeline by copying `evaluator.py:104-114`. Mechanically
  easy; it is a governance-control design decision (D6).
- Fixing `lint-imports` by moving the worker or adding an exemption (D4).
- Deleting the redundant `chore/import-linter-orphan-check` branch — its content is already
  in HEAD, but S6 forbids branch deletion.
