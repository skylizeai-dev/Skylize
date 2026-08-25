# Secrets Manager — placeholders; populate values after first apply via console or CLI.
# aws secretsmanager put-secret-value --secret-id <arn> --secret-string '<value>'

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "/${var.project}/${var.environment}/DATABASE_URL"
  description             = "Admin Postgres DSN (migrations only)"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "database_app_url" {
  name                    = "/${var.project}/${var.environment}/DATABASE_APP_URL"
  description             = "Non-superuser Postgres DSN for runtime (skylize_app role)"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "redis_url" {
  name                    = "/${var.project}/${var.environment}/REDIS_URL"
  description             = "Redis connection URL"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "hmac_secret" {
  name                    = "/${var.project}/${var.environment}/HMAC_SECRET"
  description             = "HMAC-SHA256 secret for n8n webhooks"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "langfuse_secret_key" {
  name                    = "/${var.project}/${var.environment}/LANGFUSE_SECRET_KEY"
  description             = "Langfuse secret key for LLM observability"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "governance_signing_key" {
  name                    = "/${var.project}/${var.environment}/GOVERNANCE_SIGNING_KEY_PEM"
  description             = "ECDSA P-384 private key PEM for governance token signing"
  recovery_window_in_days = 0
}

# HS256 signing key for the human-user access/refresh JWT pair.
# BOOT-CRITICAL: src/skylize/config.py `_require_jwt_secret_when_prod` raises
# inside Settings() when dev_auth is false and this is empty, and Settings() is
# constructed at module import of skylize.edge.gateway, so uvicorn exits before
# it ever binds a port. The task definition runs SKYLIZE_DEV_AUTH=false, so
# this is not optional there.
# SHELL ONLY — created empty on purpose. Populating it is an operations step
# (MVP_GAP_ANALYSIS O1) and the value must never be committed.
resource "aws_secretsmanager_secret" "jwt_secret" {
  name                    = "/${var.project}/${var.environment}/JWT_SECRET"
  description             = "HS256 signing key for user access/refresh JWTs (SKYLIZE_JWT_SECRET)"
  recovery_window_in_days = 0
}

# DB password separate so RDS can reference it
resource "aws_secretsmanager_secret" "db_password" {
  name                    = "/${var.project}/${var.environment}/DB_PASSWORD"
  description             = "RDS master password"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = "REPLACE_ME_BEFORE_APPLY"

  lifecycle {
    ignore_changes = [secret_string]
  }
}
