# Epic: User-Auth + Credential-Vault Buildout

**Goal:** make `GET/POST /api/v1/auth/*` and `/api/v1/credentials/*` live so the
committed console auth guard becomes *functional* (not just fail-closed) and
orgs can manage provider secrets.

**Status:** NOT STARTED. Scoped out of the "mount auth+credentials" ticket after
Phase 1 showed the routers depend on an absent/broken persistence +
composition-root + config layer. Do not start without an owner + the
attack-surface sign-off (mounting turns unreachable endpoints live).

---

## Why this is an epic, not a wiring task

The console guard (`website/src/proxy.ts` + `.../console/workflows/route.ts`,
committed in `25fd405c`) validates callers against `GET /api/v1/auth/me`. That
endpoint — and `/credentials/*` — cannot be mounted today because:

- `routes/auth.py` and `memory/identity.py` are **not on the mainline**; they
  exist only in commit `3e1dca3f` (mis-bundled into the knowledge commit).
- The **user-auth DAL is broken**: `dal/users.py` imports `UserRow` /
  `RefreshTokenRow` from `dal/ports.py`, which defines neither (only
  `TenantUserRow`). There is **no `UserRepository` protocol** and **no
  `InMemoryUserRepository`**, so the `backend="memory"` path (all tests) can't
  build user-auth at all.
- The `Container` (`bootstrap.py`) exposes **neither `user_auth` nor
  `credential_vault`**, yet the routers call `container.user_auth.*` /
  `container.credential_vault.*`.
- `config.py` has **no `jwt_secret`, JWT TTLs, or Fernet credential key**.

## Current inventory

**Committed / functional**
- Console n8n guard (fail-closed until `/auth/me` is live).
- `deps.get_credential_resolve_limiter` — import-only reader that mirrors
  `get_rate_limiter`; `app.state.credential_resolve_limiter` is not yet
  constructed (see step D8).

**Present but unmounted WIP (import-clean)**
- `routes/credentials.py`, `app/credentials/vault.py`,
  `app/credentials/encryption.py` (`FernetEncryptor`), `dal/credentials.py`
  (`CredentialRepository` + `Pg` + `InMemory`).
- `app/auth/user_service.py` (`UserAuthService`), `app/auth/passwords.py`,
  `app/auth/tokens.py`, `dal/users.py` (`PgUserRepository`, import-broken — see B3).

## Work items

### A. Recover the two mis-bundled files (decide: cherry-pick vs reimplement)
1. `src/skylize/edge/routes/auth.py` (register/login/refresh/me) — from `3e1dca3f`.
2. `src/skylize/memory/identity.py` (`validate_identifier`, `InvalidIdentifier`)
   — from `3e1dca3f`; imported by `auth.py`.
   > `git show 3e1dca3f:src/skylize/edge/routes/auth.py` etc. Cherry-picking just
   > these two is cleanest; confirm they carry no other `3e1dca3f`-only deps.

### B. DAL — user persistence
3. `dal/ports.py`: define `UserRow`, `RefreshTokenRow`, and the `UserRepository`
   Protocol. Match the method set `UserAuthService`/`PgUserRepository` use:
   `create_user`, `get_by_email`, `get_by_id`, `list_by_org`, `update_last_login`,
   `store_refresh_token`, `get_refresh_token`, `revoke_refresh_token`.
4. `dal/memory.py`: add `InMemoryUserRepository` (+ in-memory refresh-token
   store), mirroring `InMemoryCredentialRepository`. Required for `backend="memory"`.
5. Verify `PgUserRepository` implements the full protocol (add any missing methods).

### C. Config — secrets (existing `SKYLIZE_*` env pattern; Railway; no new infra)
6. `config.py Settings` — add, mirroring `governance_signing_key_pem`
   (default empty, **fail closed in production when unset**):
   - `jwt_secret: str = ""`  → `SKYLIZE_JWT_SECRET`
   - `jwt_access_token_ttl_minutes: int = 15`
   - `jwt_refresh_token_ttl_days: int = 30`
   - `credential_encryption_key: str = ""`  → `SKYLIZE_CREDENTIAL_ENCRYPTION_KEY`
     (Fernet key; generate via `FernetEncryptor.generate_key()`)
   Inject through Railway env exactly like `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` /
   `SKYLIZE_ANTHROPIC_API_KEY`.
   > **Token mint↔verify reconciliation (blocker for `/me` 200):**
   > `app/auth/tokens.py` mints access tokens with `jwt_secret`, but
   > `edge/auth.py build_request_context` verifies the production path with
   > `jose` against `oidc_jwks_url`. `/me` and the console guard must accept
   > `UserAuthService`-minted tokens — either verify HS256 with `jwt_secret` in
   > `build_request_context`, or route minting through the OIDC path. Resolve
   > before wiring.

### D. Composition root — `bootstrap.py`
7. Add `Container` fields + construction (both backends):
   - `user_auth: UserAuthService(repo=<user repo>, settings=settings)`
   - `credential_vault: CredentialVault(encryptor=FernetEncryptor(settings.credential_encryption_key), repo=<cred repo>, audit=audit)`
   memory → `InMemory*`; postgres → `Pg*` (`db`).
8. Gateway lifespan: `app.state.credential_resolve_limiter = RateLimiter(10)`
   (10/min per org — `credentials.py` contract), mirroring `app.state.rate_limiter`.

### E. Mount + client + data
9. `gateway.py`: `from .routes import auth, credentials`;
   `include_router(auth.router)`; `include_router(credentials.router)`.
   **Attack-surface change** — register/login/refresh are intentionally public;
   `/me` requires a valid token; all `/credentials/*` are owner/admin + org-scoped.
10. `website/src/lib/workflow-build.ts` client bearer token — **already committed**
    in the safe-subset commit.
11. Migrations: ensure `users`, `refresh_tokens`, `org_credentials` tables exist
    (`0010_org_credentials.py`, `0011_users.py` present as untracked WIP — verify).
12. Land the `/resolve` hardening in the SAME PR: keep the live working-tree
    version (owner/admin, docstring, operator→403) or apply
    `audits/pending_resolve_hardening.patch`. Do **not** double-apply.

## Build order (dependency-first)
1. C6 config → 2. B3 ports (unblocks `dal/users.py`) → 3. B4/B5 repos →
4. A recover `auth.py`+`identity.py` → 5. C token reconciliation →
6. D7/D8 Container + lifespan limiter → 7. E9/E11 mount + migrations →
8. Verify: `credentials.py` imports (done); `login → /me 200`; `operator →
/resolve 403`; `unauth POST /api/console/workflows 401`; full workflow-build flow.

## Security considerations
- Mounting makes previously-unreachable endpoints live — treat as an
  attack-surface change; land `/resolve` hardening in the same PR.
- Every credential + `/me` access is org-scoped via `RequestContext.org_id`
  (never client-supplied).
- Secrets via `SKYLIZE_*` env (Railway); production fail-closed when unset,
  mirroring `governance_signing_key_pem`.
- Never log decrypted credential values (`CredentialVault.retrieve` contract).
