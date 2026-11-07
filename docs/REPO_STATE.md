# REPO_STATE - derived facts only

**Derived at 19b149e1ae8c630206df5f8bdb7acca6133fbd64, 2026-11-07.**

Every number below sits next to the exact command that produced it. All commands
run from the repository root (bash syntax; on PowerShell use the Bash tool or
translate). If a command's output differs from the figure, the figure is stale:
re-run it and update the header SHA. Code is ground truth; anything that could
not be derived by a command is listed under UNVERIFIED or was dropped.

The previous, hand-written version of this file (dated 2026-07-29/31, describing
commit 834153c9 / 4c6f4511) is superseded. It is not deleted from history:
`MSYS_NO_PATHCONV=1 git show 19b149e1ae8c630206df5f8bdb7acca6133fbd64:docs/REPO_STATE.md`
(its suite counts, branch inventory, open-defect list and stale-claim list were
all measured against a different commit and are NOT carried forward).

---

## 1. Derived from the repository

| Fact | Value | Command (from repo root) |
|---|---|---|
| Alembic head revision | `0037` (`0037_notifications_fk_cascade.py`, down_revision `0036`) | `ls migrations/versions/*.py \| sort \| tail -1` and `grep -E '^(revision\|down_revision)' migrations/versions/0037_notifications_fk_cascade.py` |
| Alembic migration files | 37 | `ls migrations/versions/*.py \| wc -l` |
| Agent contracts registered | 33 | `PYTHONPATH=src python -c "from skylize.contracts.registry import MVP_REGISTRY; print(len(MVP_REGISTRY.all()))"` |
| FastAPI routers | 23 | `grep -c "app.include_router" src/skylize/edge/gateway.py` |
| CI jobs in ci.yml | 5: `unit`, `roster`, `console-black`, `website`, `integration` | `grep -nE '^  [a-z][a-z0-9-]*:$' .github/workflows/ci.yml` (job keys are the matches after `jobs:` at line 8; the `push:` match at line 4 is under `on:`) |
| Workflow files | 2: `ci.yml`, `deploy-staging.yml` | `ls .github/workflows` |
| ADR files | 6 (`0001`..`0006`) | `ls docs/architecture/adr` |

What each count means (so it is not re-interpreted later):

- **Agent contracts**: `len(MVP_REGISTRY.all())`, where `MVP_REGISTRY = AgentRegistry(ALL_MVP_CONTRACTS)` is the governed roster (`src/skylize/contracts/registry.py:130`; contract modules live in `src/skylize/contracts/mvp/`). It is the runtime count of registered `AgentContract`s, not a count of docs/specs on the roster.
- **FastAPI routers**: number of `app.include_router(...)` calls in `create_app()` (`src/skylize/edge/gateway.py:64` defines it; the calls are at lines 89-119). All are unconditional statements in that function; the `/health` route is a plain route, not a router, and is not counted. No other file in `src/` calls `include_router` (`grep -rn include_router src --include=*.py`, excluding gateway.py: no hits).
- **CI jobs**: top-level keys under `jobs:` in `.github/workflows/ci.yml` (job keys at lines 13, 43, 68, 97, 122). `deploy-staging.yml` has its own jobs (`lint-and-test`, `integration-test`, `build-and-push`, `deploy`; lines 21, 74, 143, 191) and is a separate workflow named "Deploy -> Staging", not part of CI.

Decision-engine selection, for orientation (re-verify the line before relying on it): `src/skylize/bootstrap.py:755` is the `settings.decision_engine != "inline"` interlock (CLAUDE.md ARCHITECTURE CONSTRAINTS describes it as fail-closed). OD7 in `docs/decisions/2026-11-07_owner_rulings.md` re-scopes ADR-0004 (LATER; neither the ADR nor CLAUDE.md has been edited).

## 2. Facts recorded by T1 orchestrator (not derived by this file's author)

Attributed as given; evidence is stated, outcomes not guessed.

1. **Deploy -> Staging has never gone green** (recorded by T1 orchestrator). Workflow `.github/workflows/deploy-staging.yml`; all runs before T1 failed. T1's run on the #35 merge commit `19b149e` is run id `42406222420`; outcome: see T1 report. Evidence: GitHub Actions run history (re-derive with `gh run list --workflow deploy-staging.yml`, read-only).
2. **The repo has 0 GitHub Actions secrets and 0 variables** (recorded by T1 orchestrator), while the deploy workflow references `secrets.AWS_GITHUB_ACTIONS_ROLE_ARN` at `.github/workflows/deploy-staging.yml:156` and `:203`. Re-run at derivation time (2026-11-07):
   - `gh api repos/skylizeai-dev/Skylize/actions/secrets --jq .total_count` -> `0`
   - `gh api repos/skylizeai-dev/Skylize/actions/variables --jq .total_count` -> `0`
   - `grep -n AWS_GITHUB_ACTIONS_ROLE_ARN .github/workflows/deploy-staging.yml` -> lines 156 and 203.
3. **Remote archive refs created by T1 (OD12)** (recorded by T1 orchestrator). Command: `git ls-remote origin 'refs/heads/archive/*'`. Output at derivation time matches the orchestrator's list:

   | Ref | SHA |
   |---|---|
   | archive/backup/pre-rebase-toolproxy-20260911-202443 | 7155c4ce3a37d9191bbece3e24de04a44d91cdad |
   | archive/docs/manifest-escalation-reconciliation | 251795e0dadf0448d6a6cf5b4c92c0c3e018c295 |
   | archive/orphan-819145e | 819145ef8f84da22c1f28588b520fc26bf9b3006 |
   | archive/orphan-8a1df90 | 8a1df90de896365cd5af5c1272bb93fbe1b331a3 |
   | archive/work/pr23 | de6c73df55d0aca4085f1727b54aee5848414833 |
   | archive/work/pr24 | f030a153f6857338a9d720c89b5ea7a60dd60872 |

## 3. UNVERIFIED / not re-measured at this commit

Dropped from the previous version because no cheap command derives them here, or they need services or long runs. Re-measure before quoting any of them:

- Test suite tally (passed/skipped/failed). Needs the full pytest run with the four `SKYLIZE_TEST_*` vars; not run for this regeneration. Use `powershell -ExecutionPolicy Bypass -File scripts/ci_unit_gate.ps1` (CLAUDE.md TESTING).
- Static-gate figures (mypy file count, import-linter file/dependency counts, orphan-module allowlist size). Produced by `scripts/ci_unit_gate.ps1`; not run here.
- Branch topology, local-only commit counts, stranded/superseded branch inventory, worktree count. These were true of the old author's clone only; derive with `git branch -vv`, `git log --oneline --all --not --remotes`, `git worktree list` on the machine in question.
- Open-defect list, stale-claim list, NOT WIRED list, governance outcome distribution (the old "9 approve / 12 defer over 21 agents" is stale: the registry now holds 33 contracts), environment-variable behaviour table, owner-decisions-outstanding list. All were hand-written claims about an older commit; none is re-verified here. Treat the old text as claims to test against code.
- Whether `SKYLIZE_DECISION_ENGINE="opa"` is still gated: CLAUDE.md says it is; the interlock location above is the starting point, behaviour not exercised here.

## 4. Pointers

- `CLAUDE.md` (repo root): durable orientation; points here for changing state.
- `docs/decisions/2026-11-07_owner_rulings.md`: owner rulings OD1-OD12 and their statuses.
- `docs/audits/_137_agent_evidence_audit.md`: moved here from `docs/03_agents/` by OD12 so the manifest generator (`scripts/gen_manifest.js`) does not treat it as an agent.
