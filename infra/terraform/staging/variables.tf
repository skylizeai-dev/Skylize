variable "project" {
  description = "Project name"
  type        = string
  default     = "skylize"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "staging"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of AZs (exactly 2)"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "acm_cert_arn" {
  description = "ACM certificate ARN for HTTPS listener"
  type        = string
  # Replace with real cert ARN before apply:
  # aws acm request-certificate --domain-name staging.skylize.ai --validation-method DNS
  default = "arn:aws:acm:us-east-1:ACCOUNT_ID:certificate/PLACEHOLDER"
}

variable "container_cpu" {
  description = "ECS task CPU units"
  type        = number
  default     = 512
}

variable "container_memory" {
  description = "ECS task memory MiB"
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Number of ECS tasks"
  type        = number
  default     = 1
}
