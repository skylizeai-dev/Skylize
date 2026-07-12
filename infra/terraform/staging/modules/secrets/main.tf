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
