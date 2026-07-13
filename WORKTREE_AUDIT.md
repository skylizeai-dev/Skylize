# Worktree Audit — 2026-07-14 (read-only)

Audited from `chore/overnight-2026-07-14` (extends `release/console-m1` @ 9f798744).
**No worktree was modified and no branch was merged.** Conflict detection used
`git merge-tree --write-tree` (in-memory trial merge). "behind/ahead" is
relative to the overnight branch HEAD at audit time.

Two branches' *content* was absorbed into the overnight branch during this
session via `git cherry-pick -x` (the source branches and worktrees themselves
are untouched): `feat/workflow-repository-postgres` and
`chore/import-linter-orphan-check`.

## Summary

| Worktree | Branch | Behind / Ahead | Conflicts | Risk | Recommendation |
|---|---|---|---|---|---|
| skylize-fix-c1 | fix/c1-safety-docs | 18 / 0 | — | none | Fully merged. Delete branch + worktree after confirmation. |
| skylize-fix-c2 | fix/c2-temporal-docs | 12 / 1 | clean | low | Amend stale "Integration status" prose, then merge. |
| skylize-fix-c3 | fix/c3-investor-status | 19 / 1 | clean | low | Merge only after human sign-off (self-labeled DRAFT, investor-facing). |
| skylize-wt-console-integration | fix/console-env-retry | 20 / 0 | — | none | Fully merged. Delete branch + worktree after confirmation. |
| tenant-isolation-port (.claude/worktrees) | feat/tenant-isolation-port | 26 / 2 | **2 files** | **medium** | Rebase with careful content-gate-preserving resolution; full test run. |
| skylize-fix-h1 | fix/h1-crewai-removal | 13 / 0 | — | none | Fully merged. Delete branch + worktree after confirmation. |
| skylize-fix-h2 | fix/h2-n8n-doc | 18 / 0 | — | none | Fully merged. Delete branch + worktree after confirmation. |
| skylize-fix-h4 | fix/h4-gemini-claims | 12 / 1 | clean | low | Merge as-is (docs-only, verified still accurate). |
| skylize-fix-dal-ports | fix/dal-ports-workflow-repo | 11 / 0 | — | none | Fully merged (via 7f3ed3e2). Delete branch + worktree after confirmation. |
| skylize-import-linter-fix | chore/import-linter-orphan-check | 12 / 1 | ci.yml (resolved) | none* | Content cherry-picked as 9c6abb05 with both CI steps kept; check passes (190 modules). Close branch after confirmation. |
| skylize-workflow-repo-impl | feat/workflow-repository-postgres | 11 / 1 | clean | none | Patch-equivalent content already in HEAD (cherry-picked as 0e73d603; `git cherry` shows `-`). Close branch + remove worktree after confirmation. |

\* risk was "low, one trivial ci.yml adjacent-insert conflict" — that exact resolution (keep both steps) is already applied on the overnight branch.

## Per-branch detail (branches with unmerged commits)

### fix/c2-temporal-docs — low risk, merge after doc amendment
- Unmerged: `63d8f9f4 fix(docs): C-2 correct Temporal Cloud status from deferred to committed, document LangGraph/Temporal split`
- Changes: `docs/02_architecture/tech_stack.md`, `docs/architecture/03_agent_runtime.md` (docs only, merge-tree clean).
- **Semantic staleness**: the branch's "Integration status" prose asserts WorkflowRepository/WorkflowRunStepRow are "not yet present" in `dal/ports.py` and describes the judge activity as un-wired — both invalidated by HEAD (f1043406 ports, 0e73d603 Postgres repo + migration 0010, 754ea7aa concrete LLMJudge). No textual conflict, but merging as-is publishes architecture docs that contradict the code.
- **Recommendation**: update the two stale passages on the branch (especially the "not yet defined in dal/ports.py — a known gap" sentence in 03_agent_runtime.md), then merge. The core C-2 correction (Temporal committed, not deferred) remains valid and wanted.

### fix/c3-investor-status — low risk, gated on human sign-off
- Unmerged: `2ae91bf4 draft(docs): C-3 align investor materials with actual auth/console status — NEEDS HUMAN SIGN-OFF before external use`
- Changes: two files under `docs/10_investor_materials/` (docs only, merge-tree clean, no overlap with HEAD).
- Nothing in HEAD's 19 newer commits contradicts the auth-gap/console-mock claims, but the commit is explicitly a draft.
- **Recommendation**: merge as-is technically safe; **do not** let it reach external use until a human confirms the console-mock-data and auth-not-started claims are still current.

### fix/h4-gemini-claims — low risk, merge as-is
- Unmerged: `78ff4095 fix(docs): H-4 mark Gemini as roadmap, not implemented`
- Changes: 4 architecture/integration docs (merge-tree clean; HEAD never touched them). Verified `pyproject.toml` at HEAD still has no Gemini/Google SDK, so the correction remains accurate.
- **Recommendation**: merge as-is; no rebase needed.

### feat/tenant-isolation-port — MEDIUM risk, needs careful rebase
- Unmerged: `14fda5a6 feat(security): tenant isolation — injective identity, tenant-keyed qdrant IDs, knowledge hardening` and `22f96316 test: cover knowledge_ingestion wiring in bootstrap`
- 19 files (11 src/scripts, 8 tests). **Real conflicts in 2 files**: `src/skylize/bootstrap.py` and `src/skylize/memory/knowledge_ingestion.py`.
- **Why it matters**: since the merge-base, HEAD threaded `LLMContentGate` into `KnowledgeIngestionService` (the `content_gate` kwarg + `self._gate.check(content)` in `ingest()`) and into the composition root — and this overnight branch further changed bootstrap (DecisionEngine wiring, `Container.llm`). The branch rewrote the same module (org_id-required ingest, chunked `ingest_document`, new Qdrant API) and moved `KnowledgeIngestionService` construction earlier in `build_container`. **A careless resolution would silently drop the prompt-injection screen from the new ingest paths — a security regression.**
- **Recommendation**: rebase onto the integration branch; in bootstrap construct `LLMContentGate` *before* `KnowledgeIngestionService`/`DeliverableService` and keep the `content_gate=` kwarg; in knowledge_ingestion keep `self._gate.check(content)` on BOTH the rewritten `ingest()` and the new chunked `ingest_document()` path. Then run the full suite (its new `test_bootstrap_wiring.py` will now exercise the DecisionEngine-starting `build_container`).

### chore/import-linter-orphan-check — absorbed (cherry-picked 9c6abb05)
- Its single commit (`0e0e919a`, all-modules-importable CI check) was cherry-picked onto the overnight branch; the predicted ci.yml adjacent-insert conflict was resolved exactly as the audit recommended (keep both steps: Forbidden imports, then All modules importable). The check passes against HEAD: 190 modules import cleanly.
- **Recommendation**: close the branch / remove the worktree after confirming; nothing left to merge.

### feat/workflow-repository-postgres — absorbed (cherry-picked 0e73d603)
- `git cherry` marks its only commit `-` (patch-equivalent already in HEAD); diff of all three files between branch tip and HEAD is empty.
- **Recommendation**: close the branch / remove the worktree after confirming; nothing left to merge.

## Fully merged (0 ahead) — housekeeping only
`fix/c1-safety-docs`, `fix/console-env-retry`, `fix/h1-crewai-removal`,
`fix/h2-n8n-doc`, `fix/dal-ports-workflow-repo` are all 0 ahead: every commit
already reachable from the overnight branch. Their worktrees exist only as
checkouts. Safe to `git worktree remove` + branch-delete after confirmation —
**not done here (audit-only mandate).**
