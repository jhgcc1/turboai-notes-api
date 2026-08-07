variable "project" {
  type    = string
  default = "turboai-notes"
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
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
    security_groups = [aws_security_group.apprunner_connector.id, aws_security_group.bastion.id]
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
    cidr_blocks = ["0.0.0.0/0"] # tighten in real ops
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "apprunner_connector" {
  name   = "${local.name}-apprunner"
  vpc_id = aws_vpc.main.id
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
  identifier                 = "${local.name}-pg"
  engine                     = "postgres"
  engine_version             = "16"
  instance_class             = "db.t4g.micro"
  allocated_storage          = 20
  max_allocated_storage      = 50
  db_name                    = "turbo_notes"
  username                   = var.db_username
  password                   = var.db_password
  db_subnet_group_name       = aws_db_subnet_group.db.name
  vpc_security_group_ids     = [aws_security_group.db.id]
  publicly_accessible        = false
  skip_final_snapshot        = var.environment != "prod"
  backup_retention_period    = var.environment == "prod" ? 7 : 1
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

resource "aws_iam_instance_profile" "bastion" {
  name = "${local.name}-bastion"
  role = aws_iam_role.bastion.name
}

resource "aws_instance" "bastion" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = "t4g.nano"
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.bastion.id]
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.bastion.name
  user_data                   = <<-EOF
    #!/bin/bash
    dnf install -y postgresql15
  EOF
  tags = merge(local.tags, { Name = "${local.name}-bastion" })
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_ecr_repository" "api" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  tags                 = local.tags
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/turboai/notes/${var.environment}/api"
  retention_in_days = var.environment == "prod" ? 14 : 7
  tags              = local.tags
}

resource "aws_iam_role" "apprunner_ecr" {
  name = "${local.name}-apprunner-ecr"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr" {
  role       = aws_iam_role.apprunner_ecr.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

resource "aws_iam_role" "apprunner_instance" {
  name = "${local.name}-apprunner-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "apprunner_logs" {
  name = "${local.name}-logs"
  role = aws_iam_role.apprunner_instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.api.arn}:*"
    }]
  })
}

resource "aws_apprunner_vpc_connector" "connector" {
  vpc_connector_name = "${local.name}-connector"
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.apprunner_connector.id]
  tags               = local.tags
}

resource "aws_apprunner_service" "api" {
  service_name = local.name
  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr.arn
    }
    image_repository {
      image_identifier      = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
      image_repository_type = "ECR"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          ENVIRONMENT           = var.environment == "prod" ? "production" : "staging"
          DEBUG                 = "false"
          SECRET_KEY            = var.django_secret_key
          POSTGRES_DB           = "turbo_notes"
          POSTGRES_USER         = var.db_username
          POSTGRES_PASSWORD     = var.db_password
          POSTGRES_HOST         = aws_db_instance.postgres.address
          POSTGRES_PORT         = "5432"
          ALLOWED_HOSTS         = "*"
          COOKIE_SECURE         = "true"
          CLOUDWATCH_ENABLED    = "true"
          CLOUDWATCH_LOG_GROUP  = aws_cloudwatch_log_group.api.name
          AWS_REGION            = var.aws_region
          CORS_ALLOWED_ORIGINS  = "https://${aws_cloudfront_distribution.web.domain_name}"
          CSRF_TRUSTED_ORIGINS  = "https://${aws_cloudfront_distribution.web.domain_name}"
          FRONTEND_URL          = "https://${aws_cloudfront_distribution.web.domain_name}"
        }
      }
    }
    auto_deployments_enabled = false
  }
  instance_configuration {
    cpu               = "256"
    memory            = "512"
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }
  network_configuration {
    egress_configuration {
      egress_type       = "VPC"
      vpc_connector_arn = aws_apprunner_vpc_connector.connector.arn
    }
  }
  health_check_configuration {
    protocol            = "HTTP"
    path                = "/api/health/"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
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

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  default_root_object = "index.html"
  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3-web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
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
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }
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

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "4xxStatusResponses"
  namespace           = "AWS/AppRunner"
  period              = 300
  statistic           = "Sum"
  threshold           = 50
  treat_missing_data  = "notBreaching"
  dimensions = {
    ServiceName = aws_apprunner_service.api.service_name
  }
  tags = local.tags
}

output "api_url" {
  value = "https://${aws_apprunner_service.api.service_url}"
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

output "log_group" {
  value = aws_cloudwatch_log_group.api.name
}
