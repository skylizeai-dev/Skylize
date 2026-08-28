# Day-1 provisioning inventory

Read-only audit. No secret values, no code changes. Every claim below cites
`file:line`; where a claim in the source task did not verify against current
repo state, that is reported explicitly rather than assumed.

**Path note:** the task that requested this doc specified
`docs/07_ops/DAY1_PROVISIONING.md`. That directory does not exist —
`docs/07_security/` is the real `07_*` slot, and the operations docs live in
`docs/08_operations/` (`incident_response.md`, `monitoring.md`,
`observability.md`, `opa_staging_bring_up.md`). This file is placed at
`docs/08_operations/DAY1_PROVISIONING.md` to match the existing convention
instead of creating a new, inconsistent `07_ops/` directory.

---

## 1. AWS Secrets Manager shells

Defined in [infra/terraform/staging/modules/secrets/main.tf](infra/terraform/staging/modules/secrets/main.tf).
There are **8** `aws_secretsmanager_secret` resources, not 7. Of those, **7 are
created genuinely empty** (populate via `aws secretsmanager put-secret-value`
per the file's header comment); the 8th (`db_password`) ships a placeholder
value with `lifecycle.ignore_changes`, so it is not an empty shell in the same
sense.

| Terraform resource | Secret name (ARN name component) | Empty at apply? | Wired into the ECS task def? | Consumer in `src/` |
|---|---|---|---|---|
| `database_url` | `/${project}/${environment}/DATABASE_URL` | yes | yes — `SKYLIZE_DB_URL`, [infra/terraform/staging/modules/ecs/main.tf:127-129](infra/terraform/staging/modules/ecs/main.tf#L127-L129) | `Settings.db_url`, [src/skylize/config.py:36](src/skylize/config.py#L36) |
| `database_app_url` | `.../DATABASE_APP_URL` | yes | yes — `SKYLIZE_DB_APP_URL`, [ecs/main.tf:135-138](infra/terraform/staging/modules/ecs/main.tf#L135-L138) | `Settings.db_app_url`, [config.py:37](src/skylize/config.py#L37); enforced non-empty and distinct from `db_url` on a non-memory backend by `_require_distinct_app_dsn_on_a_real_backend`, [config.py:244-278](src/skylize/config.py#L244-L278) |
| `redis_url` | `.../REDIS_URL` | yes | yes — `SKYLIZE_REDIS_URL`, [ecs/main.tf:139-142](infra/terraform/staging/modules/ecs/main.tf#L139-L142) | `Settings.redis_url`, [config.py:38](src/skylize/config.py#L38) |
| `hmac_secret` | `.../HMAC_SECRET` | yes | yes — mapped to `SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET`, [ecs/main.tf:143-146](infra/terraform/staging/modules/ecs/main.tf#L143-L146) (note: Terraform resource name says "HMAC_SECRET" for n8n webhooks, consumed under a differently-named env var) | `Settings.knowledge_webhook_secret`, [config.py:146](src/skylize/config.py#L146); gates `/api/v1/knowledge/ingest` closed (503) when unset per the same line's comment |
| `langfuse_secret_key` | `.../LANGFUSE_SECRET_KEY` | yes | **no** — `secret_arns[4]` is never referenced in [ecs/main.tf](infra/terraform/staging/modules/ecs/main.tf)'s `secrets` block (only indices `0,1,2,3,5` plus the separately named `jwt_secret_arn` are used, [ecs/main.tf:125-159](infra/terraform/staging/modules/ecs/main.tf#L125-L159)) | `Settings.langfuse_secret_key`, [config.py:163](src/skylize/config.py#L163) exists but has no way to receive a value in the deployed task — **finding, not fixed here** |
| `governance_signing_key` | `.../GOVERNANCE_SIGNING_KEY_PEM` | yes | yes — `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM`, [ecs/main.tf:147-150](infra/terraform/staging/modules/ecs/main.tf#L147-L150) | `Settings.governance_signing_key_pem`, [config.py:50](src/skylize/config.py#L50); consumed at [src/skylize/app/governance/keys.py:57](src/skylize/app/governance/keys.py#L57), which raises when unset |
| `db_password` | `.../DB_PASSWORD` | **no** — seeded with `"REPLACE_ME_BEFORE_APPLY"` and `ignore_changes`, [secrets/main.tf:61-68](infra/terraform/staging/modules/secrets/main.tf#L61-L68) | its ARN is in `secret_arns[6]` (output list) but, like `langfuse_secret_key`, is not referenced by name in the ECS `secrets` block shown | RDS master password (Terraform-internal, not an app-level `Settings` field) |
| `jwt_secret` | `.../JWT_SECRET` | yes | yes — via the dedicated `var.jwt_secret_arn` output, not the positional list, [ecs/main.tf:155-158](infra/terraform/staging/modules/ecs/main.tf#L155-L158); outputs.tf:8 | `Settings.jwt_secret`, [config.py:73](src/skylize/config.py#L73); required when `dev_auth=false` per `_require_jwt_secret_when_prod`, [config.py:216-224](src/skylize/config.py#L216-L224) |

**Finding:** the ECS task definition ([infra/terraform/staging/modules/ecs/main.tf:161-164](infra/terraform/staging/modules/ecs/main.tf#L161-L164)) also flags `SKYLIZE_APP_DB_PASSWORD` as "STILL MISSING, AND STILL A BOOT BLOCKER" — migration 0003 reads it from the environment and there is no Secrets Manager entry or task-def wiring for it at all. That is a 9th piece of missing provisioning, separate from the 8 shells above.

**Finding:** `langfuse_secret_key` (and, in a different way, `db_password`, which is RDS-internal rather than an app setting) has a Secrets Manager shell and a `Settings` field, but no path connecting them — populating the AWS secret would have no effect on a deployed container today.

---

## 1b. Resolution status (2026-08-28)

*The inventory above was read-only. This section records what has since been
wired, and corrects two claims in it that did not survive re-verification.*

### RESOLVED — `SKYLIZE_APP_DB_PASSWORD` is now provisioned

The 9th missing item is closed in terraform. Four additive changes:

| File | Change |
|---|---|
| [modules/secrets/main.tf](infra/terraform/staging/modules/secrets/main.tf) | new `aws_secretsmanager_secret "app_db_password"` -> `/${project}/${environment}/APP_DB_PASSWORD`. **Shell only** — no `secret_version`, no value in the repo. |
| [modules/secrets/outputs.tf](infra/terraform/staging/modules/secrets/outputs.tf) | named output `app_db_password_arn`, plus an append at index `[8]` of `secret_arns` so `modules/iam` grants `GetSecretValue` on it. |
| [modules/ecs/variables.tf](infra/terraform/staging/modules/ecs/variables.tf) | `variable "app_db_password_arn"` — named, not a positional index, per the module's own warning. |
| [modules/ecs/main.tf](infra/terraform/staging/modules/ecs/main.tf) | `SKYLIZE_APP_DB_PASSWORD` added to the task definition's `secrets` block; the "STILL MISSING, AND STILL A BOOT BLOCKER" comment replaced with the resolved state and the remaining operations step. |
| [staging/main.tf](infra/terraform/staging/main.tf) | passes `app_db_password_arn = module.secrets.app_db_password_arn`. |

Settings plumbing: `Settings.app_db_password` ([src/skylize/config.py](src/skylize/config.py)).
Declared so the variable is documented and discoverable; its real consumer is
`os.environ` inside [migrations/versions/0003_app_role_rls_subject.py:49](migrations/versions/0003_app_role_rls_subject.py#L49),
which runs under alembic and never constructs `Settings`. Also added to
[.env.example](.env.example).

**Deliberately not validated as required.** Migration 0003 is idempotent and its
`ALTER` branch does not reset an existing role's password, so a deploy against a
database where `skylize_app` already exists is correct with this unset. A hard
validator would fail those deploys for no reason. The case it guards is a fresh
database.

`terraform validate` passes on the modified configuration.

**Still an operations step.** The shell is empty; terraform never writes a value
into it. `APP_DB_PASSWORD` and the password embedded in `DATABASE_APP_URL` must
be the same string, and **nothing verifies that they agree** — a mismatch is an
authentication failure at boot, not a degraded mode. Populate both together.

### CORRECTION — `SKYLIZE_DB_APP_URL` fail-loud was already implemented

The task driving this section asked for fail-loud behaviour to replace a silent
fallback to the superuser. **That was already in place, twice over**, and no
behaviour change was needed:

1. [config.py `_require_distinct_app_dsn_on_a_real_backend`](src/skylize/config.py) —
   raises inside `Settings()` when `backend != "memory"` and `db_app_url` is
   empty **or** equal to `db_url`. `Settings()` is constructed at module import
   of `skylize.edge.gateway`, so uvicorn exits before binding a port.
2. [bootstrap.py `verify_app_role_is_rls_subject`](src/skylize/bootstrap.py) —
   connects and reads the role's own `pg_roles` row, refusing to start on a
   `SUPERUSER` or `BYPASSRLS` role. This catches what the string comparison
   cannot: a different spelling of the same superuser DSN.

The `runtime_db_url` property does still contain `self.db_app_url or self.db_url`,
but that branch is **unreachable on any real backend** — only `SKYLIZE_BACKEND=memory`
reaches it, where there is no Postgres and no RLS to bypass. The comments around
it described it as a dev-acceptable production fallback, which was stale and
misleading; they have been corrected to state the interlock. No logic changed.

`.github/workflows/deploy-staging.yml` needs **no change**. Its
`SKYLIZE_APP_DB_PASSWORD: testpass` at lines 115 and 137 belongs to the
integration-test job's ephemeral Postgres service container, not to any deployed
environment. The deployed container's `SKYLIZE_DB_APP_URL` comes from the
registered ECS task definition (`secret_arns[1]`); the deploy job downloads that
task definition and swaps **only the image**
(`amazon-ecs-render-task-definition`, lines 207-220), so environment wiring is
terraform's job alone.

### 3d — dead secrets: one is dead, one is not

**`langfuse_secret_key` — genuinely inert. Recommendation: mark unused, do NOT wire.**

- No `langfuse` client is ever constructed from settings. `bootstrap.py` contains
  no reference to langfuse at all.
- The two consumers take an injected client defaulting to `None`
  ([anthropic_adapter.py:226](src/skylize/adapters/llm/anthropic_adapter.py#L226),
  [decision_engine/pipeline.py:118](src/skylize/decision_engine/pipeline.py#L118)),
  and both no-op when it is `None`. The only callers that pass one are three
  unit tests passing a mock.
- `langfuse` is **not a declared dependency** — it appears in `pyproject.toml`
  only inside a prose comment.

Wiring the env var would connect a secret to a client nobody builds, from a
package that is not installed. That produces a deployment which *looks*
instrumented and is not — the same class of false capability signal ADR-0004's
correction addresses. Leave the Secrets Manager shell (harmless, empty, already
IAM-granted) and treat `Settings.langfuse_public_key` / `langfuse_secret_key` as
**declared but unconsumed** until a real Langfuse client is constructed in
`bootstrap.py`. Populating the AWS secret today has no effect and should not be
part of Day-1.

**`db_password` — NOT dead. The audit above was wrong about this one.**

Section 1 listed it alongside `langfuse_secret_key` as having "no path
connecting" the secret to anything. It has one — it is simply not an ECS path.
[modules/rds/main.tf:29-31](infra/terraform/staging/modules/rds/main.tf#L29-L31)
reads it through a `data "aws_secretsmanager_secret_version"` block and
[:46](infra/terraform/staging/modules/rds/main.tf#L46) assigns it as the RDS
instance's master `password`. It is consumed at `terraform apply` time by
Terraform itself, not at runtime by the container, which is exactly why it has —
and should have — no task-definition wiring.

**Recommendation: neither wire it nor mark it unused. Mark it Terraform-internal.**
It does, however, still ship the placeholder `"REPLACE_ME_BEFORE_APPLY"` with
`ignore_changes`, so the master password must be replaced before the RDS
instance is created or the database comes up with a known literal password.

---

## 2. Governance signing key generation vs. consumption

[scripts/gen_governance_key.py](scripts/gen_governance_key.py):
- Output: a PKCS8 **PEM** of a newly generated **ECDSA P-384** key pair
  ([scripts/gen_governance_key.py:31](scripts/gen_governance_key.py#L31), `Curve.P384`).
- Format: PEM text to stdout ([scripts/gen_governance_key.py:33](scripts/gen_governance_key.py#L33), `pair.private_pem(...).decode()`); optional password-based encryption via `--password` ([scripts/gen_governance_key.py:26-32](scripts/gen_governance_key.py#L26-L32)).
- The script's own docstring says to inject the PEM via the secrets manager as `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` ([scripts/gen_governance_key.py:6](scripts/gen_governance_key.py#L6)).

Consumer trace:
- `Settings.governance_signing_key_pem: str = ""`, [src/skylize/config.py:50](src/skylize/config.py#L50) — env var `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` (prefix from `env_prefix="SKYLIZE_"`, [config.py:19](src/skylize/config.py#L19)).
- Read and enforced in [src/skylize/app/governance/keys.py:57](src/skylize/app/governance/keys.py#L57): raises when no key is configured ("No governance signing key configured. Set SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM ...").
- Wired to the real AWS secret in the ECS task definition: `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` <- `var.secret_arns[5]` (the `governance_signing_key` resource), [infra/terraform/staging/modules/ecs/main.tf:147-150](infra/terraform/staging/modules/ecs/main.tf#L147-L150).

**A consumer exists and the wiring is complete end to end** (script -> secret -> task def -> `Settings` -> `keys.py` enforcement). This is not a gap.

---

## 3. `.env.example` vs `config.py` Settings — undocumented fields

`Settings` fields (from `Settings.model_fields`, [src/skylize/config.py:17-298](src/skylize/config.py#L17-L298)) not present as `SKYLIZE_<FIELD>=` lines anywhere in [.env.example](.env.example):

- `token_ttl_minutes`
- `oidc_jwks_url`
- `oidc_audience`
- `request_context_ttl_seconds`
- `jwt_access_token_ttl_minutes`
- `jwt_refresh_token_ttl_days`
- `slack_bot_token`
- `slack_approval_channel_id`
- `dlq_after_retries`
- `temporal_address`
- `temporal_namespace`
- `temporal_task_queue`
- `search_provider`
- `search_api_key`
- `llm_demo_mode`
- `langfuse_public_key`
- `langfuse_secret_key`
- `mem0_api_key`
- `qdrant_url`
- `qdrant_api_key`
- `llm_model_default`
- `llm_model_fast`
- `llm_model_reasoning`
- `llm_retry_max_attempts`
- `llm_retry_base_delay_seconds`
- `llm_retry_max_delay_seconds`
- `llm_retry_jitter_seconds`
- `llm_timeout_seconds`

Of note: `slack_bot_token` / `slack_approval_channel_id` back the HITL Slack
notifier referenced in the latest commit (`e990e43`) and are mutually
required — `bootstrap.py`'s `resolve_slack_notifier_config` fails closed if
only one is set (per the comment at [config.py:87-95](src/skylize/config.py#L87-L95)) — but neither appears
in `.env.example`, so a deployer has no local template for them.

No edits made to `.env.example` per the task's instruction — list only.

---

## 4. `.env` CORS parse failure — does not currently reproduce

The task described a live failure: "`config.py` `cors_origins` raises
`SettingsError` from `DotEnvSettingsSource`." I could not reproduce this
against the repository's current `.env` (gitignored, present on disk,
9474 bytes as of this audit):

- [.env:48](.env#L48): `SKYLIZE_CORS_ORIGINS=[]` — this is valid JSON and
  parses cleanly into `list[str] = []`.
- Loading `Settings()` directly against the working tree's `.env` succeeds
  (`Settings().cors_origins == []`); no exception is raised.
- `python-dotenv`'s raw parse of the file (`dotenv_values(".env")`) also
  returns `'[]'` for that key without error.
- `.env` is not tracked (`.gitignore:12`), so there is no historical committed
  version to diff against, and no `SettingsError`/`cors_origins` hits appear
  in `docs/` or the existing audit doc (`REPO_STATE_AUDIT_2026-08-12.md`) that
  would corroborate a prior failing value.

**Finding: the premise does not verify against current repo state.** Either
the malformed line was already fixed in this working tree before this audit
ran, or the failure exists in an environment/`.env` variant not present here.
`pydantic_settings` 2.14.2 (installed) parses `list[str]` fields from a
dotenv value by feeding the raw string through JSON decoding — the format
that **would** trigger `SettingsError` is a bare, unquoted, non-JSON list,
e.g. `SKYLIZE_CORS_ORIGINS=https://console.skylize.com` or a trailing comma /
single-quoted `['https://console.skylize.com']`, neither of which is valid
JSON. The satisfying form is a double-quoted JSON array:
`SKYLIZE_CORS_ORIGINS=["https://console.skylize.com"]` (matches the working
example already in [.env.example:47](.env.example#L47) and the code comment
at [config.py:66](src/skylize/config.py#L66)). No fix applied — reporting
only, per the task's instruction not to fix this item.
