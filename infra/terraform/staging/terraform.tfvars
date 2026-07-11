# Staging environment variable overrides.
# Commit this file; do NOT put secrets here.

project            = "skylize"
environment        = "staging"
aws_region         = "us-east-1"
vpc_cidr           = "10.0.0.0/16"
availability_zones = ["us-east-1a", "us-east-1b"]
container_cpu      = 512
container_memory   = 1024
desired_count      = 1

# Replace with real ACM cert ARN:
#   aws acm request-certificate --domain-name staging.skylize.ai --validation-method DNS
acm_cert_arn = "arn:aws:acm:us-east-1:ACCOUNT_ID:certificate/PLACEHOLDER"
