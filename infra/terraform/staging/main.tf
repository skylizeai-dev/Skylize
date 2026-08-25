terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "skylize-terraform-state-staging"
    key            = "staging/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "skylize-terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "skylize"
      Environment = "staging"
      ManagedBy   = "terraform"
    }
  }
}

module "vpc" {
  source = "./modules/vpc"

  project     = var.project
  environment = var.environment
  vpc_cidr    = var.vpc_cidr
  azs         = var.availability_zones
}

module "ecr" {
  source = "./modules/ecr"

  project     = var.project
  environment = var.environment
}

module "secrets" {
  source = "./modules/secrets"

  project     = var.project
  environment = var.environment
}

module "iam" {
  source = "./modules/iam"

  project         = var.project
  environment     = var.environment
  aws_region      = var.aws_region
  aws_account_id  = data.aws_caller_identity.current.account_id
  secret_arns     = module.secrets.secret_arns
  ecr_repository_arn = module.ecr.repository_arn
}

module "rds" {
  source = "./modules/rds"

  project             = var.project
  environment         = var.environment
  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  ecs_sg_id           = module.ecs.ecs_sg_id
  db_password_secret_arn = module.secrets.db_password_secret_arn
}

module "elasticache" {
  source = "./modules/elasticache"

  project            = var.project
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  ecs_sg_id          = module.ecs.ecs_sg_id
}

module "alb" {
  source = "./modules/alb"

  project           = var.project
  environment       = var.environment
  vpc_id            = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids
  acm_cert_arn      = var.acm_cert_arn
}

module "ecs" {
  source = "./modules/ecs"

  project                = var.project
  environment            = var.environment
  aws_region             = var.aws_region
  vpc_id                 = module.vpc.vpc_id
  private_subnet_ids     = module.vpc.private_subnet_ids
  alb_target_group_arn   = module.alb.target_group_arn
  alb_sg_id              = module.alb.alb_sg_id
  # BOOTSTRAP PLACEHOLDER, not the tag that ends up running. CI publishes
  # SHA-only tags now (:latest was removed from .github/workflows/deploy-staging.yml
  # on 2026-07-31 so a container that cannot boot stops being published as
  # :latest), so this reference resolves to nothing until someone pushes a
  # :latest by hand. That is survivable only because the deploy job re-renders
  # the task definition with the SHA-tagged image and the service carries
  # `lifecycle { ignore_changes = [task_definition] }`. A `terraform apply`
  # against an empty ECR still creates a revision 1 whose image cannot be
  # pulled. Point this at a real tag before relying on terraform alone to
  # stand the service up.
  ecr_image_uri          = "${module.ecr.repository_url}:latest"
  task_execution_role_arn = module.iam.task_execution_role_arn
  task_role_arn           = module.iam.task_role_arn
  secret_arns             = module.secrets.secret_arns
  jwt_secret_arn          = module.secrets.jwt_secret_arn
  db_host                 = module.rds.db_endpoint
  redis_host              = module.elasticache.redis_endpoint
}

data "aws_caller_identity" "current" {}
