variable "project" { type = string }
variable "environment" { type = string }
variable "aws_region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "alb_target_group_arn" { type = string }
variable "alb_sg_id" { type = string }
variable "ecr_image_uri" { type = string }
variable "task_execution_role_arn" { type = string }
variable "task_role_arn" { type = string }
variable "secret_arns" { type = list(string) }

# Named, NOT an index into secret_arns. main.tf below reads that list
# positionally (secret_arns[0]..[5]) to wire the task definition's `secrets`
# block, which means a reorder in modules/secrets/outputs.tf would silently
# inject the wrong value into the wrong environment variable. New secrets the
# task definition needs get their own variable so the wiring is named.
variable "jwt_secret_arn" { type = string }

variable "db_host" { type = string }
variable "redis_host" { type = string }
# These three were written as `{ type = number; default = 512 }`. HCL2 has no
# statement separator: a single-line block may carry AT MOST ONE argument, so
# `terraform init` aborted while loading this file with "Invalid character /
# Invalid single-argument block definition" -- before contacting AWS at all.
# That is independent proof this configuration had never been run.
variable "container_cpu" {
  type    = number
  default = 512
}

variable "container_memory" {
  type    = number
  default = 1024
}

variable "desired_count" {
  type    = number
  default = 1
}
