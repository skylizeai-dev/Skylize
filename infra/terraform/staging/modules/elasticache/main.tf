resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project}-${var.environment}-redis-subnet"
  subnet_ids = var.private_subnet_ids
  tags       = { Name = "${var.project}-${var.environment}-redis-subnet" }
}

resource "aws_security_group" "redis" {
  name        = "${var.project}-${var.environment}-redis-sg"
  description = "Allow Redis from ECS tasks only"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [var.ecs_sg_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-${var.environment}-redis-sg" }
}

resource "aws_elasticache_cluster" "main" {
  cluster_id           = "${var.project}-${var.environment}-redis"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = aws_elasticache_parameter_group.main.name
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]
  port                 = 6379

  snapshot_retention_limit = 1
  snapshot_window          = "05:00-06:00"

  tags = { Name = "${var.project}-${var.environment}-redis" }
}

resource "aws_elasticache_parameter_group" "main" {
  name   = "${var.project}-${var.environment}-redis7"
  family = "redis7"

  parameter {
    name  = "appendonly"
    value = "yes"
  }

  parameter {
    name  = "appendfsync"
    value = "everysec"
  }
}
