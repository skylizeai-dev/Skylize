# Deployment Strategy (Index)

**Status:** Development index (points to the consolidated source of truth)
**Owner:** `vp_engineering` · `director_devops` · `director_platform`
**Related:** [../architecture/06_deployment_architecture.md](../architecture/06_deployment_architecture.md) · [coding_standards.md](./coding_standards.md) · [../02_architecture/service_map.md](../02_architecture/service_map.md) · [../08_operations/monitoring.md](../08_operations/monitoring.md)

---

## 1. Purpose

This file is the **entry point** to how Skylize is built, promoted, and deployed
from within the development folder. The authoritative, detailed plan — deployable
units, MVP→Scale topology, migration triggers, CI/CD, backup/replay, DR — lives in
[../architecture/06_deployment_architecture.md](../architecture/06_deployment_architecture.md).
This index summarizes it for engineers and never diverges from it.

## 2. Architectural role

Deployment follows one rule: **the same logical architecture at every tier; only
the substrate changes, behind ports already present in MVP**
([../architecture/01_final_stack.md §5](../architecture/01_final_stack.md#5-mvp-stack-vs-scale-stack)).
One image set; tiers differ by config, not code.

## 3. Environments

| Env | Substrate |
|---|---|
| `local` | Docker Compose, single Postgres/Redis/Qdrant/MinIO |
| `staging` | prod-shaped, smaller; replayable from prod-shaped fixtures |
| `production` | MVP (Compose/VM) → Scale (Kubernetes) |

Promotion is **image-tag based**; config differs per env, code does not
([../architecture/06_deployment_architecture.md §3](../architecture/06_deployment_architecture.md#3-environments)).

## 4. CI/CD pipeline (summary)

```
PR → lint + type-check + unit tests → build image (pinned, SBOM, signed)
   → contract tests (schemas, events, AgentContract registry loads, OPA policies)
   → deploy to staging → smoke + replay tests → manual gate
   → deploy to production (rolling)
```

- **GitHub Actions**, OIDC to cloud (no long-lived keys).
- **Contract gate:** the build fails if any `AgentContract` is invalid or any
  event schema / OPA policy breaks compatibility
  ([coding_standards.md §7](./coding_standards.md#7-testing)).
- **Rolling deploys** with health checks; governance and DB-migration steps are
  ordered and reversible.

## 5. MVP → Scale (summary)

Migrate a component **only** when its trigger fires; each move is a substitution
behind an existing port (`EventBus`, `VectorStore`, `ObjectStore`, `LLMGateway`,
DAL) — domain and agent logic never change. Full trigger table:
[../architecture/06_deployment_architecture.md §6](../architecture/06_deployment_architecture.md#6-migration-triggers-mvp--scale).

## 6. Backup, replay & DR (summary)

- Postgres PITR; Redis AOF + archive-before-trim to S3; Qdrant rebuildable from
  Postgres + `memory.*` replay; audit/governance object-locked 7-year floor.
- DR is largely "**replay the durable log**," not "hope the backup is fresh"
  ([../architecture/06_deployment_architecture.md §10](../architecture/06_deployment_architecture.md#10-disaster-recovery)).

## 7. Ownership & evolution

- **Owner:** `vp_engineering` (overall), `director_devops` (pipeline/IaC),
  `director_platform` (runtime).
- **Evolution:** when the consolidated deployment architecture changes, this index
  is updated in the same PR. The one-image-set / substitution-behind-ports
  invariants are permanent.
