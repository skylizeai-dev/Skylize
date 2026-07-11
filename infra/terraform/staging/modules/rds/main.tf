resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.environment}-db-subnet"
  subnet_ids = var.private_subnet_ids
  tags       = { Name = "${var.project}-${var.environment}-db-subnet" }
}

resource "aws_security_group" "rds" {
  name        = "${var.project}-${var.environment}-rds-sg"
  description = "Allow Postgres from ECS tasks only"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.ecs_sg_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-${var.environment}-rds-sg" }
}

data "aws_secretsmanager_secret_version" "db_password" {
  secret_id = var.db_password_secret_arn
}

resource "aws_db_instance" "main" {
  identifier = "${var.project}-${var.environment}-postgres"

  engine               = "postgres"
  engine_version       = "15.6"
  instance_class       = "db.t3.medium"
  allocated_storage    = 20
  max_allocated_storage = 100
  storage_type         = "gp3"
  storage_encrypted    = true

  db_name  = "skylize"
  username = "skylize"
  password = data.aws_secretsmanager_secret_version.db_password.secret_string

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  multi_az               = false  # single-AZ for staging
  publicly_accessible    = false
  deletion_protection    = false  # allow destroy in staging
  skip_final_snapshot    = true

  backup_retention_period = 3
  backup_window           = "03:00-04:00"
  maintenance_window      = "sun:04:00-sun:05:00"

  parameter_group_name = aws_db_parameter_group.main.name

  tags = { Name = "${var.project}-${var.environment}-postgres" }
}

resource "aws_db_parameter_group" "main" {
  name   = "${var.project}-${var.environment}-pg15"
  family = "postgres15"

  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"  # log queries > 1s
  }
}
