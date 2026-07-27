# Import-linter exemptions

Live register of every `ignore_imports` exemption carried by the
import-linter contracts in `pyproject.toml`. An exemption that is not
recorded here, with an owner decision and a testable removal condition, is
not a valid exemption.

## 1. Temporal worker entrypoint → bootstrap / dal.workflows

- **Contract:** "Application logic contains no SQL (depends on dal ports only)"
- **Ignored edges (exact, no wildcards):**
  - `skylize.app.orchestrator.temporal.worker -> skylize.bootstrap`
  - `skylize.app.orchestrator.temporal.worker -> skylize.dal.workflows`
- **Decision:** 2026-07-27, owner decision during W1 trunk reconciliation.
  A narrow, documented, time-boxed exemption was chosen over rewriting or
  relocating paused code, and over any silent weakening of the contract.
- **Why:** `worker.py` is a process entrypoint / composition root — like
  `edge` and `bootstrap` it constructs concretes at startup
  (`build_container()`, `PgWorkflowRepository(container.db)`) — but it sits
  inside `skylize.app`, so its two direct imports transitively reach the
  contract's forbidden targets along three paths: worker → bootstrap →
  `dal.connection` → `asyncpg`; worker → bootstrap → `dal.repositories`;
  worker → `dal.workflows` → `dal.connection`. The subsystem
  `app.orchestrator.temporal.*` is unwired/paused code on no live request
  path, consistent with the mypy `ignore_errors` override for the same tree
  in `pyproject.toml`. Only the two edges out of the worker module are
  ignored; every other `skylize.app` module remains fully checked.
- **REMOVAL CONDITION (testable trigger):** delete this exemption the moment
  the Temporal worker is wired to a live path, or when the concrete judge
  activity is implemented — whichever comes first.
- **Provenance:** violating module introduced by commit `1ef9281d`
  ("feat(temporal): worker entrypoint"). The SHA of the commit that added
  the exemption is recoverable via `git blame` on the `ignore_imports` block
  in `pyproject.toml`.
