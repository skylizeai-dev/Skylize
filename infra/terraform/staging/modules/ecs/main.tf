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

      # Every entry here is load-bearing at BOOT, not at first request. The
      # process constructs Settings() at module import of skylize.edge.gateway
      # (edge/gateway.py `app = create_app()`), so a missing or contradictory
      # value exits uvicorn before it binds :8000 and the ALB health check
      # below can never pass.
      environment = [
        # backend != "memory" turns on three fail-closed validators in
        # src/skylize/config.py: dev_auth must be false, db_app_url must be
        # non-empty, and db_app_url must differ from db_url.
        { name = "SKYLIZE_BACKEND", value = "postgres" },
        # Required by `_forbid_dev_auth_on_a_real_backend`. Dev auth trusts the
        # X-Dev-Org / X-Dev-User / X-Dev-Roles headers verbatim, so leaving it
        # true on postgres is an open door to every tenant; Settings refuses to
        # construct on that combination rather than shipping it.
        { name = "SKYLIZE_DEV_AUTH", value = "false" },

        # STAGING LLM POSTURE: DEMO MODE (owner decision, 2026-07-31).
        # src/skylize/bootstrap.py raises LLMConfigurationError during the
        # lifespan when neither SKYLIZE_ANTHROPIC_API_KEY nor this flag is set,
        # which kills the container at startup. There is no Anthropic key in
        # any Secrets Manager shell today, and a staging environment that
        # cannot start proves nothing, so demo mode is set EXPLICITLY here
        # rather than left to an implicit fallback (there is no implicit
        # fallback -- the code fails closed by design).
        #
        # Demo mode wires DemoLLMAdapter, which logs a WARNING on every call
        # and returns canned output for the 7 of 21 agents that have a payload;
        # the rest raise DemoResponseUnavailable naming themselves. It is
        # independent of SKYLIZE_BACKEND -- the adapter takes no database and
        # the LLM branch in bootstrap.py never reads settings.backend -- so
        # demo mode and backend=postgres are compatible.
        #
        # TO SWITCH TO A REAL PROVIDER: create an ANTHROPIC_API_KEY shell in
        # modules/secrets/main.tf, populate it (operations step, never in
        # code), wire it into the `secrets` block below as
        # SKYLIZE_ANTHROPIC_API_KEY, and flip this to "false". Real egress also
        # needs the model_pricing rows from migration 0013 and an org spend
        # ceiling row, or the adapter refuses the call rather than guessing a
        # price.
        { name = "SKYLIZE_LLM_DEMO_MODE", value = "true" },

        # PYTHONPATH=/app/src REMOVED 2026-07-31. The image installs skylize
        # into site-packages and no longer copies a loose src/ tree, so this
        # pointed at a directory that does not exist. It was previously the
        # only reason the container could import the package at all -- see the
        # header of the root Dockerfile.
      ]

      # POSITIONAL INDEXING WARNING: var.secret_arns[N] is ordered by
      # modules/secrets/outputs.tf. Reordering that list rewires a secret into
      # the wrong environment variable with no error anywhere. New secrets get
      # a named variable instead (see SKYLIZE_JWT_SECRET below).
      secrets = [
        {
          name      = "SKYLIZE_DB_URL"
          valueFrom = var.secret_arns[0]  # DATABASE_URL
        },
        # Must resolve to the non-superuser skylize_app role, NOT the RDS
        # master user. Two independent checks reject the master user: the
        # Settings string comparison (db_app_url == db_url) and, past that, a
        # live pg_roles probe in bootstrap.py (verify_app_role_is_rls_subject)
        # that refuses to start if the runtime role is SUPERUSER or BYPASSRLS.
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
        # ADDED 2026-07-31. Without this, Settings() raises
        # "SKYLIZE_JWT_SECRET must be set when dev_auth is disabled" at module
        # import and uvicorn never starts. The shell is created empty in
        # modules/secrets/main.tf; populating it is an operations step.
        {
          name      = "SKYLIZE_JWT_SECRET"
          valueFrom = var.jwt_secret_arn
        },
        # ADDED 2026-08-28. Closes the boot blocker described below. Read by
        # migration 0003 during this container's `alembic upgrade head`, BEFORE
        # uvicorn starts, to create skylize_app with a password instead of
        # without one. Named variable, not a secret_arns index.
        {
          name      = "SKYLIZE_APP_DB_PASSWORD"
          valueFrom = var.app_db_password_arn
        },
      ]

      # RESOLVED 2026-08-28 -- SKYLIZE_APP_DB_PASSWORD is wired above.
      #
      # What it was: this container's CMD is `alembic upgrade head && uvicorn
      # ...`, and migration 0003 reads SKYLIZE_APP_DB_PASSWORD from the
      # environment to decide whether skylize_app is created
      # `LOGIN PASSWORD '<pw>'` or a bare `LOGIN` with no password at all
      # (0003_app_role_rls_subject.py:49-50). Nothing in this terraform creates
      # the skylize_app role -- modules/rds/main.tf provisions only the
      # `skylize` master user -- so the role came into existence here, on first
      # boot, with NO password. The DATABASE_APP_URL secret then carried a
      # password RDS would reject, and bootstrap.py failed at
      # `await db.connect()`. The ordering (migration creates the role before
      # uvicorn connects) was always fine; the password was not.
      #
      # STILL AN OPERATIONS STEP, NOT AN AUTOMATED ONE. The shell is created
      # empty in modules/secrets/main.tf and terraform never writes a value into
      # it. Two secrets must be populated with the SAME password before the
      # first boot of a fresh database:
      #   /<project>/<env>/APP_DB_PASSWORD   -- the bare password
      #   /<project>/<env>/DATABASE_APP_URL  -- a DSN embedding that same password
      # Nothing verifies they agree. A mismatch is an authentication failure at
      # boot, not a degraded mode.

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
