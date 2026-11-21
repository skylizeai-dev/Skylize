# Worktree & process inventory -- sky-hyg2-20261120

Generated 2026-11-20. Read-only: nothing below was deleted, moved, or merged by
this run. 21/21 worktrees present at inventory time covered (20 N/N is stale --
`git worktree list` returned 21 entries including this run's own).

## Premise correction (reported, not acted on)

The task brief states `Skylize-wt-sky-sec-1` is the ACTIVE T1 worktree and says
not to touch it. At inventory time it **no longer exists**: not in
`git worktree list`, directory gone from disk, and its `.git/worktrees/`
admin entry is also gone (a clean `git worktree remove`, not a crash --
nothing orphaned). This run did not remove it; the only worktree this run
removed, ever, is its own (`Skylize-wt-sky-hyg2-1`, at the end, per G5.7).
Its branch, `feat/myday-atomic-rotation`, still exists on origin at `fff74a0`
(newer than the `5924717` seen mid-run-1), and is PR #52 (OPEN). Most likely
explanation: the T1 session finished and cleaned up its own worktree normally.
No data loss detected.

## Worktrees

| Path | Branch | HEAD | Date | Dirty | Remote branch | Merged to main | Label |
|---|---|---|---|---|---|---|---|
| `Skylize` (primary) | fix/notifications-fk-cascade | 2503da0 | 2026-09-28 | 4 files (docs/vault/.obsidian/*, .vscode/, awesome-free-llm-apis/ -- pre-existing, not from this run) | YES | YES (ancestor of main) | **PRIMARY-TREE** (in active use, do not delete) |
| skylize-agent-contracts | feat/agent-contracts-121-fill | f88e156 | 2026-09-20 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote branch already gone, i.e. merged+cleaned) |
| skylize-audit-readiness | feat/audit-product-readiness | 9e7b382 | 2026-10-05 | clean | YES | NO | **HAS-UNMERGED-WORK** -- 1 commit ahead of main: `9e7b382 docs(audit): product readiness vs 3 bars @ 0fc9904`. Note: OD13 closed PR #30 for this branch at SHA `5b57433`, but HEAD here is `9e7b382` -- commits were added to this branch AFTER that closure; not re-litigated here. |
| skylize-console-billing | feat/console-billing | 8a61607 | 2026-09-20 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-console-knowledge | feat/console-knowledge | e363037 | 2026-09-20 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-console-models | feat/console-models | 3104a93 | 2026-09-20 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-console-notifications | feat/console-notifications | 243b8e5 | 2026-09-20 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-console-org-settings | feat/console-org-settings | 9fe6671 | 2026-09-21 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-console-permissions | feat/console-permissions | ecbf2c5 | 2026-09-21 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-console-security | feat/console-security | a9ddccc | 2026-09-20 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-console-workflows-history | feat/console-workflows-history | 8f62721 | 2026-09-20 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-e2e-fix | fix/e2e-deliverable-schema-drift | 5aa268b | 2026-09-08 | clean | NO | YES | **SAFE-TO-DELETE** (ancestor-of-main; remote gone) |
| skylize-hitl-resumption | (detached HEAD) | 0fc9904 | 2026-09-29 | clean | NO (detached, no branch) | YES | **SAFE-TO-DELETE** (detached, ancestor-of-main, nothing unique to preserve) |
| skylize-manifest-recon | docs/manifest-escalation-reconciliation | 251795e | 2026-09-14 | clean | YES | NO | **HAS-UNMERGED-WORK** -- 1 commit ahead: `251795e docs(agents): record why the manifest and the contracts disagree` |
| skylize-myday-csrf | feat/myday-csrf | ea00875 | 2026-10-14 | clean | YES | NO | **HAS-UNMERGED-WORK** -- 1 commit ahead: `ea00875 fix(my-day): refuse cross-site state-changing requests (login CSRF)` |
| skylize-myday-identity | feat/myday-identity-path | 5f3bd88 | 2026-09-26 | clean | NO | NO | **UNKNOWN** -- not merged AND no remote branch (local-only). Could be superseded local work or something not yet pushed; 1 commit ahead of main (`5f3bd88 feat(my-day): per-user identity path for the BFF...`). Not enough evidence here to call it safe or active. |
| skylize-myday-revocation-design | feat/myday-revocation-design | f954332 | 2026-10-14 | clean | YES | NO | **HAS-UNMERGED-WORK** -- 2 commits ahead: `f954332 docs(my-day): record owner decisions on session revocation design`, `1d177b0 docs(my-day): design server-side session revocation (owner review)` |
| skylize-pr-merge-scratch | work/pr22 | 7492e6a | 2026-09-29 | clean | NO | NO | **UNKNOWN** -- local-only (no remote), not merged, 3 commits ahead, branch name (`work/pr22`) suggests scratch/manual merge work, not a feature branch. Purpose not re-derivable from this run alone. |
| skylize-state-sync | chore/state-sync-20261120 | de94ea7 | 2026-11-20 (today) | clean | YES | NO | **ACTIVE** -- today's state-sync run, the parent state this run was built on. Do not delete. |
| skylize-stripe-foundation | feat/stripe-oauth-refund-foundation | f9f05df | 2026-09-19 | clean | YES | NO | **HAS-UNMERGED-WORK** -- 14 commits ahead of main (Stripe create_refund tool + tests + CI fixes). Matches OD8 ("Stripe: wire in test mode... [later]") -- plausibly the pending "later" work itself. |
| `Skylize-wt-sky-hyg2-1` (this run) | feat/decision-records-20261120 | 1d995d9 | 2026-11-20 (today) | clean | YES | NO | **ACTIVE** (this run's own worktree; removed by this run at the end per G5.7, not left behind) |

N counted: 21 worktrees at inventory time (20 pre-existing + this run's own). All 21 accounted for above.

## Running claude-code CLI processes

cwd could not be read for any PID via available tooling (Win32 does not expose a
process's current directory without kernel-level inspection) -- all UNKNOWN per
the task's own allowance for this case.

| PID | Started | Parent PID | CWD |
|---|---|---|---|
| 8304 | 2026-11-20 18:03:49 | 26488 | UNKNOWN |
| 16736 | 2026-11-20 18:03:55 | 29300 | UNKNOWN |
| 28712 | 2026-11-20 18:04:03 | 25640 | UNKNOWN |
| 25928 | 2026-11-20 18:04:07 | 15824 | UNKNOWN |
| 30060 | 2026-11-20 18:04:11 | 18372 | UNKNOWN |
| 28508 | 2026-11-20 18:05:19 | 26176 | UNKNOWN |

6 claude-code CLI processes running at inventory time, all started within a
~90s window (18:03:49-18:05:19) -- consistent with several sessions/tabs open
in parallel, one of which is this run.

## Owner delete-command list (NOT RUN -- for the owner to review and execute)

Only the SAFE-TO-DELETE rows above. Each removes the worktree only; the local
branch ref is left for the owner to delete separately if desired (not assumed
here, since merged-but-kept-local branches may still be wanted as history markers).

```
git worktree remove "C:/Users/HP/Documents/Work/skylize-agent-contracts"
git worktree remove "C:/Users/HP/Documents/Work/skylize-console-billing"
git worktree remove "C:/Users/HP/Documents/Work/skylize-console-knowledge"
git worktree remove "C:/Users/HP/Documents/Work/skylize-console-models"
git worktree remove "C:/Users/HP/Documents/Work/skylize-console-notifications"
git worktree remove "C:/Users/HP/Documents/Work/skylize-console-org-settings"
git worktree remove "C:/Users/HP/Documents/Work/skylize-console-permissions"
git worktree remove "C:/Users/HP/Documents/Work/skylize-console-security"
git worktree remove "C:/Users/HP/Documents/Work/skylize-console-workflows-history"
git worktree remove "C:/Users/HP/Documents/Work/skylize-e2e-fix"
git worktree remove "C:/Users/HP/Documents/Work/skylize-hitl-resumption"
git worktree prune
```
