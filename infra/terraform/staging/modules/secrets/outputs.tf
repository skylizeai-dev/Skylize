output "database_url_arn" { value = aws_secretsmanager_secret.database_url.arn }
output "database_app_url_arn" { value = aws_secretsmanager_secret.database_app_url.arn }
output "redis_url_arn" { value = aws_secretsmanager_secret.redis_url.arn }
output "hmac_secret_arn" { value = aws_secretsmanager_secret.hmac_secret.arn }
output "langfuse_secret_key_arn" { value = aws_secretsmanager_secret.langfuse_secret_key.arn }
output "governance_signing_key_arn" { value = aws_secretsmanager_secret.governance_signing_key.arn }
output "db_password_secret_arn" { value = aws_secretsmanager_secret.db_password.arn }

output "secret_arns" {
  value = [
    aws_secretsmanager_secret.database_url.arn,
    aws_secretsmanager_secret.database_app_url.arn,
    aws_secretsmanager_secret.redis_url.arn,
    aws_secretsmanager_secret.hmac_secret.arn,
    aws_secretsmanager_secret.langfuse_secret_key.arn,
    aws_secretsmanager_secret.governance_signing_key.arn,
    aws_secretsmanager_secret.db_password.arn,
  ]
}
