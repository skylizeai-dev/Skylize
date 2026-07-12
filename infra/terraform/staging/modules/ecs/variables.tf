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
variable "db_host" { type = string }
variable "redis_host" { type = string }
variable "container_cpu" { type = number; default = 512 }
variable "container_memory" { type = number; default = 1024 }
variable "desired_count" { type = number; default = 1 }
