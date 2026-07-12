variable "project" { type = string }
variable "environment" { type = string }
variable "aws_region" { type = string }
variable "aws_account_id" { type = string }
variable "secret_arns" { type = list(string) }
variable "ecr_repository_arn" { type = string }
