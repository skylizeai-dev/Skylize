# Worktree closeout — 2026-08-28

Recovery record for the worktree sprawl removed on 2026-08-28. At closeout the
repository had **64 registered worktrees** against a single repository: the
primary checkout, four under `.claude/worktrees/`, and 59 under
`C:/Users/HP/Documents/Work/`. All 63 non-primary checkouts were removed;
`git worktree list` now shows the primary checkout alone.

**No commits were lost.** `git worktree remove` detaches a checkout; it does not
delete the branch ref or any commit. Verified after removal: the branch list is byte-identical before and after
(130 refs both times). Every branch listed below still exists and
is still checkoutable by name. This file exists so the content of the four
`.claude/worktrees/` branches is known without re-deriving it.

Counts are measured against `main` at `51319c4` (2026-08-28).

## The four `.claude/worktrees/` entries

| Branch | Tip SHA | Ahead of main | Behind main | Unique content |
|---|---|---|---|---|
| `worktree-bus-audit-gov` | `99bb3b613a4428ce753f461bf3955654abae6f05` | 1 | 143 | ADR-0007 (event bus delivery semantics, 235 lines) + `docs/audits/2026-07_bus_delivery_audit.md` (308 lines). Docs only, no source changes. Neither file exists on `main`. |
| `worktree-bus-delivery-audit` | `0d36edc338afff3f00a14f7f62a5b21bb82348b6` | 0 | 229 | Nothing. Zero commits ahead of `main` — the checkout was a stale base with no work on it. Safe to delete the branch entirely. |
| `feat/capital-budget-reservation` | `24bc604b88e7c0f70bf96cbcc90716445bb6583d` | 1 | 142 | Transactional budget reservation, 804 insertions across `DECISIONS_PENDING.md`, `decision_engine/capital_dal.py`, `orchestrator.py`, `publisher.py`, `tests/integration/test_capital_reservation.py`. **See the deferral note below.** |
| `audit/decision-consumer-gap` | `8b3f2c44bf5e7655466b608d9c8b33bfac1aad7d` | 1 | 142 | `docs/audits/2026-07_decision_consumer_gap.md` (208 lines) — decision consumer gap + sink convergence analysis. Docs only. Not on `main`. |

All four checkouts were **clean** at removal: `git status --porcelain` returned
empty in each. No uncommitted work was discarded.

## `feat/capital-budget-reservation` — DEFERRED, and why

This branch modifies `src/skylize/decision_engine/` — the **OPA package**, not
the inline evaluator. Per CLAUDE.md's "THE TWO ENGINES" and ADR-0004, that
package is not wired into the API process: `bootstrap.py` fails closed on any
`SKYLIZE_DECISION_ENGINE` value other than `"inline"`, and the live request path
is forbidden from importing `skylize.decision_engine` at all.

So the code on this branch **targets a path that cannot execute in production
today**. Merging it would add 804 lines of unreachable logic, its integration
test would exercise a decision path no live agent walks, and the branch would
acquire the appearance of shipped capital-governance capability without any of
the behaviour.

**Ruling: deferred.** It is not abandoned — the work is real and the branch is
intact at the SHA above. It becomes mergeable when the OPA package is genuinely
wired: real Rego policy content, a live OPA server, and wire-parity with the
inline evaluator. Until then it stays a branch.

## Branches whose content should be recovered first

The three docs-only branches (`worktree-bus-audit-gov`,
`audit/decision-consumer-gap`) carry 751 lines of audit and ADR material that
exists nowhere on `main`. That content has no dependency on the OPA wiring
question and can be cherry-picked onto `main` at any time:

    git cherry-pick 99bb3b6   # ADR-0007 + bus delivery audit
    git cherry-pick 8b3f2c4   # decision consumer gap audit

## Restoring any branch as a working checkout

    git worktree add ../skylize-<name> <branch>

## Deleting the one empty branch

`worktree-bus-delivery-audit` has zero unique commits. When you are satisfied
nothing depends on the name:

    git branch -D worktree-bus-delivery-audit
