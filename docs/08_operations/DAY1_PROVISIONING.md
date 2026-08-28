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
