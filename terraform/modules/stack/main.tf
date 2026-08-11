variable "project" {
  type    = string
  default = "turboai-notes"
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "us-east-2"
}

variable "db_username" {
  type    = string
  default = "turbo"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "django_secret_key" {
  type      = string
  sensitive = true
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "frontend_bucket_force_destroy" {
  type    = bool
  default = true
}

variable "bastion_public_key" {
  type        = string
  description = "OpenSSH public key for bastion SSH. Private key stays operator-local (e.g. ~/turbo-notes-bastion.pem); never commit it."
}

variable "bastion_ssh_cidr" {
  type        = list(string)
  description = "CIDR blocks allowed to SSH (port 22) to the bastion. Prefer operator public IP /32; update env tfvars when the IP changes."
}

locals {
  name = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.${var.environment == "prod" ? 1 : 0}.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${local.name}-vpc" })
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = local.tags
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${local.name}-public-${count.index}" })
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = merge(local.tags, { Name = "${local.name}-private-${count.index}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = local.tags
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "db" {
  name   = "${local.name}-db"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id, aws_security_group.bastion.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "bastion" {
  name   = "${local.name}-bastion"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.bastion_ssh_cidr
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "alb" {
  name   = "${local.name}-alb"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "ecs_tasks" {
  name   = "${local.name}-ecs"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_db_subnet_group" "db" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private[*].id
  tags       = local.tags
}

resource "aws_db_instance" "postgres" {
  identifier             = "${local.name}-pg"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20
  max_allocated_storage  = 50
  db_name                = "turbo_notes"
  username               = var.db_username
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.db.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  skip_final_snapshot    = var.environment != "prod"
  # Free-tier accounts reject backup_retention_period > 1 (FreeTierRestrictionError).
  # Keep 1 day for both envs; raise only after upgrading off free tier.
  backup_retention_period    = 1
  deletion_protection        = var.environment == "prod"
  auto_minor_version_upgrade = true
  tags                       = local.tags
}

resource "aws_iam_role" "bastion" {
  name = "${local.name}-bastion"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "bastion_ssm" {
  role       = aws_iam_role.bastion.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "bastion" {
  name = "${local.name}-bastion"
  role = aws_iam_role.bastion.name
}

resource "aws_key_pair" "bastion" {
  key_name   = "${local.name}-bastion"
  public_key = var.bastion_public_key
  tags       = local.tags
}

resource "aws_instance" "bastion" {
  ami = data.aws_ssm_parameter.al2023.value
  # Free-tier eligible on this account (t4g.nano is not).
  instance_type               = "t4g.micro"
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.bastion.id]
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.bastion.name
  key_name                    = aws_key_pair.bastion.key_name
  user_data_replace_on_change = true

  # IMDSv2 required; hop limit 2 is enough for host SSM agent.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }

  # Wait for IMDS instance-profile credentials, then restart SSM agent.
  # Prior staging bastion stayed notconnected because the agent started before
  # credentials were available and never recovered (console: "unable to acquire credentials").
  user_data = <<-EOF
    #!/bin/bash
    set -euxo pipefail
    TOKEN=""
    ROLE=""
    for i in $(seq 1 90); do
      TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" || true)
      if [ -n "$TOKEN" ]; then
        ROLE=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
          http://169.254.169.254/latest/meta-data/iam/security-credentials/ || true)
        if [ -n "$ROLE" ]; then
          CREDS=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/$ROLE" || true)
          if echo "$CREDS" | grep -q AccessKeyId; then
            break
          fi
        fi
      fi
      sleep 2
    done
    systemctl enable amazon-ssm-agent || true
    systemctl restart amazon-ssm-agent || true
    dnf install -y postgresql15
  EOF

  depends_on = [
    aws_iam_role_policy_attachment.bastion_ssm,
    aws_iam_instance_profile.bastion,
  ]

  tags = merge(local.tags, { Name = "${local.name}-bastion" })
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_secretsmanager_secret" "django_secret_key" {
  name                    = "${local.name}-django-secret-key"
  recovery_window_in_days = 0
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "django_secret_key" {
  secret_id     = aws_secretsmanager_secret.django_secret_key.id
  secret_string = var.django_secret_key
}

resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${local.name}-db-password"
  recovery_window_in_days = 0
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = var.db_password
}

resource "aws_ecr_repository" "api" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  tags                 = local.tags
}

resource "aws_cloudwatch_log_group" "api" {
  name = "/turboai/notes/${var.environment}/api"
  # 30 days in prod so a slow-burning regression is still visible when someone
  # goes looking a few weeks later; staging only needs a short window.
  retention_in_days = var.environment == "prod" ? 30 : 7
  tags              = local.tags
}

# App Runner is closed to new customers (2026-04-30). API runs on ECS Fargate + ALB,
# with CloudFront in front for HTTPS (default cert) without a custom domain.
resource "aws_iam_role" "ecs_execution" {
  name = "${local.name}-ecs-exec"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# AmazonECSTaskExecutionRolePolicy does not grant Secrets Manager read; task
# startup needs this to resolve `secrets:` (valueFrom) entries below.
resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "${local.name}-ecs-exec-secrets"
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.django_secret_key.arn,
        aws_secretsmanager_secret.db_password.arn,
      ]
    }]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "${local.name}-ecs-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "ecs_task_logs" {
  name = "${local.name}-ecs-logs"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.api.arn}:*"
    }]
  })
}

resource "aws_lb" "api" {
  name               = "${local.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  tags               = local.tags
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"
  health_check {
    path                = "/api/health/"
    healthy_threshold   = 2
    unhealthy_threshold = 5
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }
  tags = local.tags
}

resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_ecs_cluster" "api" {
  name = local.name
  tags = local.tags
}

resource "aws_ecs_task_definition" "api" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  container_definitions = jsonencode([{
    name      = "api"
    image     = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
    essential = true
    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]
    environment = [
      { name = "ENVIRONMENT", value = var.environment == "prod" ? "production" : "staging" },
      { name = "DEBUG", value = "false" },
      { name = "POSTGRES_DB", value = "turbo_notes" },
      { name = "POSTGRES_USER", value = var.db_username },
      { name = "POSTGRES_HOST", value = aws_db_instance.postgres.address },
      { name = "POSTGRES_PORT", value = "5432" },
      { name = "ALLOWED_HOSTS", value = "*" },
      { name = "COOKIE_SECURE", value = "true" },
      # First-party via web CF /api/* proxy — Lax works in Incognito (unlike None cross-site).
      { name = "COOKIE_SAMESITE", value = "Lax" },
      # The awslogs driver below already ships stdout to this log group, so
      # watchtower would duplicate every line: double ingest cost and double
      # counting on the ERROR metric filter that drives triage.
      { name = "CLOUDWATCH_ENABLED", value = "false" },
      { name = "CLOUDWATCH_LOG_GROUP", value = aws_cloudwatch_log_group.api.name },
      { name = "SERVICE_NAME", value = "turboai-notes-api" },
      { name = "AWS_REGION", value = var.aws_region },
      # Strict per-environment frontend origin only (this stack's web CF).
      # Never "*" / localhost / the other env's URL — credentials CORS + CSRF.
      { name = "CORS_ALLOWED_ORIGINS", value = "https://${aws_cloudfront_distribution.web.domain_name}" },
      { name = "CSRF_TRUSTED_ORIGINS", value = "https://${aws_cloudfront_distribution.web.domain_name}" },
      { name = "FRONTEND_URL", value = "https://${aws_cloudfront_distribution.web.domain_name}" },
    ]
    # SECRET_KEY / POSTGRES_PASSWORD resolved from Secrets Manager at task
    # startup, never rendered into the task definition in plaintext.
    secrets = [
      { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.django_secret_key.arn },
      { name = "POSTGRES_PASSWORD", valueFrom = aws_secretsmanager_secret.db_password.arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_service" "api" {
  name            = local.name
  cluster         = aws_ecs_cluster.api.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener.api]
  tags       = local.tags
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "api" {
  enabled = true
  origin {
    domain_name = aws_lb.api.dns_name
    origin_id   = "alb-api"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }
  default_cache_behavior {
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD", "OPTIONS"]
    target_origin_id         = "alb-api"
    viewer_protocol_policy   = "redirect-to-https"
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
  tags = local.tags
}

resource "aws_s3_bucket" "web" {
  bucket        = "${local.name}-web-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.frontend_bucket_force_destroy
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${local.name}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# S3+OAC has no directory index; rewrite /login/ → /login/index.html for Next `output: "export"`.
# Do not rewrite /api/* — that path is proxied to the ALB (ordered_cache_behavior).
resource "aws_cloudfront_function" "web_spa_rewrite" {
  name    = "${local.name}-spa-rewrite"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-EOF
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      if (uri === '/api' || uri.indexOf('/api/') === 0) {
        return request;
      }
      if (uri.endsWith('/')) {
        request.uri = uri + 'index.html';
      } else if (!uri.includes('.')) {
        request.uri = uri + '/index.html';
      }
      return request;
    }
  EOF
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  default_root_object = "index.html"
  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3-web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }
  # Same distribution as the SPA so /api/* cookies are first-party (Incognito-safe).
  origin {
    domain_name = aws_lb.api.dns_name
    origin_id   = "alb-api"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }
  ordered_cache_behavior {
    path_pattern             = "api/*"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD", "OPTIONS"]
    target_origin_id         = "alb-api"
    viewer_protocol_policy   = "redirect-to-https"
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    # Host = ALB DNS (not the viewer web CF host) so the origin request matches the API CF path.
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }
  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https"
    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.web_spa_rewrite.arn
    }
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
  # No distribution-wide 403/404 → index.html: that would rewrite proxied API errors.
  # SPA deep links rely on the viewer-request rewrite to …/index.html instead.
  tags = local.tags
}

data "aws_iam_policy_document" "web_oac" {
  statement {
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_oac.json
}

# The 5xx alarm now lives in modules/observability, alongside the rest of the
# alarms and the SNS topic that routes them to the triage Lambda.

output "api_url" {
  value = "https://${aws_cloudfront_distribution.api.domain_name}"
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.api.name
}

output "ecs_service_name" {
  value = aws_ecs_service.api.name
}

output "alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "web_url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "web_bucket" {
  value = aws_s3_bucket.web.bucket
}

output "cloudfront_id" {
  value = aws_cloudfront_distribution.web.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "rds_endpoint" {
  value = aws_db_instance.postgres.address
}

output "bastion_host" {
  value = aws_instance.bastion.public_ip
}

output "bastion_instance_id" {
  value = aws_instance.bastion.id
}

output "bastion_key_name" {
  value = aws_key_pair.bastion.key_name
}

output "log_group" {
  value = aws_cloudwatch_log_group.api.name
}

output "log_group_arn" {
  value = aws_cloudwatch_log_group.api.arn
}

output "alb_arn_suffix" {
  value = aws_lb.api.arn_suffix
}

output "target_group_arn_suffix" {
  value = aws_lb_target_group.api.arn_suffix
}

output "rds_instance_id" {
  value = aws_db_instance.postgres.identifier
}
