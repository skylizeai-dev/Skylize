output "database_url_arn" { value = aws_secretsmanager_secret.database_url.arn }
output "database_app_url_arn" { value = aws_secretsmanager_secret.database_app_url.arn }
output "redis_url_arn" { value = aws_secretsmanager_secret.redis_url.arn }
output "hmac_secret_arn" { value = aws_secretsmanager_secret.hmac_secret.arn }
output "langfuse_secret_key_arn" { value = aws_secretsmanager_secret.langfuse_secret_key.arn }
output "governance_signing_key_arn" { value = aws_secretsmanager_secret.governance_signing_key.arn }
output "db_password_secret_arn" { value = aws_secretsmanager_secret.db_password.arn }
output "jwt_secret_arn" { value = aws_secretsmanager_secret.jwt_secret.arn }

# APPEND-ONLY LIST. modules/ecs/main.tf reads this POSITIONALLY
# (var.secret_arns[0] .. [5]) to build the task definition's `secrets` block, so
# reordering or inserting an entry silently rewires a secret into the wrong
# environment variable. New entries go at the END, and anything the task
# definition names should be passed to the ECS module as its own named variable
# instead (see jwt_secret_arn above and modules/ecs/variables.tf).
# The list itself is still what the IAM policies grant GetSecretValue on
# (modules/iam/main.tf), so every secret must appear here.
output "secret_arns" {
  value = [
    aws_secretsmanager_secret.database_url.arn,
    aws_secretsmanager_secret.database_app_url.arn,
    aws_secretsmanager_secret.redis_url.arn,
    aws_secretsmanager_secret.hmac_secret.arn,
    aws_secretsmanager_secret.langfuse_secret_key.arn,
    aws_secretsmanager_secret.governance_signing_key.arn,
    aws_secretsmanager_secret.db_password.arn,
    aws_secretsmanager_secret.jwt_secret.arn,
  ]
}
