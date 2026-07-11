resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.project}-${var.environment}/api"
  retention_in_days = 7
  tags              = { Name = "${var.project}-${var.environment}-logs" }
}

resource "aws_security_group" "ecs" {
  name        = "${var.project}-${var.environment}-ecs-sg"
  description = "ECS Fargate tasks"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [var.alb_sg_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-${var.environment}-ecs-sg" }
}

resource "aws_ecs_cluster" "main" {
  name = "${var.project}-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${var.project}-${var.environment}-cluster" }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE"
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project}-${var.environment}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = var.ecr_image_uri
      essential = true

      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        { name = "SKYLIZE_BACKEND", value = "postgres" },
        { name = "SKYLIZE_DEV_AUTH", value = "false" },
        { name = "PYTHONPATH", value = "/app/src" },
      ]

      secrets = [
        {
          name      = "SKYLIZE_DB_URL"
          valueFrom = var.secret_arns[0]  # DATABASE_URL
        },
        {
          name      = "SKYLIZE_DB_APP_URL"
          valueFrom = var.secret_arns[1]  # DATABASE_APP_URL
        },
        {
          name      = "SKYLIZE_REDIS_URL"
          valueFrom = var.secret_arns[2]  # REDIS_URL
        },
        {
          name      = "SKYLIZE_KNOWLEDGE_WEBHOOK_SECRET"
          valueFrom = var.secret_arns[3]  # HMAC_SECRET
        },
        {
          name      = "SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM"
          valueFrom = var.secret_arns[5]  # GOVERNANCE_SIGNING_KEY_PEM
        },
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      stopTimeout = 30
    }
  ])

  tags = { Name = "${var.project}-${var.environment}-api-taskdef" }
}

resource "aws_ecs_service" "api" {
  name            = "${var.project}-${var.environment}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 100
    base              = 1
  }

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.alb_target_group_arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 60

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  depends_on = [var.alb_target_group_arn]

  tags = { Name = "${var.project}-${var.environment}-api-service" }
}
