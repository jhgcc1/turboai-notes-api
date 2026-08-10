locals {
  name               = "${var.project}-${var.environment}"
  observability_root = "${var.repo_root}/observability"
  metric_namespace   = "turboai/notes/${var.environment}"

  # Lambda sources, minus the unit tests (they never run in AWS and only make
  # the artifact bigger).
  lambda_files = [
    for file in fileset(local.observability_root, "**/*.py") :
    file if !startswith(file, "tests/")
  ]

  # A copy of the application source travels with the function so triage can
  # quote the exact lines a traceback points at. The project is small enough
  # that apps/ + config/ costs only a few tens of kilobytes.
  repo_files = concat(
    tolist(fileset(var.repo_root, "apps/**/*.py")),
    tolist(fileset(var.repo_root, "config/**/*.py")),
  )

  tags = merge(var.tags, { Component = "observability" })
}

data "aws_caller_identity" "current" {}

data "archive_file" "triage" {
  type        = "zip"
  output_path = "${path.module}/triage-${var.environment}.zip"

  dynamic "source" {
    for_each = local.lambda_files
    content {
      content  = file("${local.observability_root}/${source.value}")
      filename = source.value
    }
  }

  dynamic "source" {
    for_each = local.repo_files
    content {
      content  = file("${var.repo_root}/${source.value}")
      filename = "repo/${source.value}"
    }
  }
}

# ---------------------------------------------------------------------------
# Notification topics
#
# Two topics on purpose: alarms fan in to the Lambda, and the Lambda publishes
# its report to a separate topic. A single topic would make the function
# subscribe to its own output and re-trigger itself indefinitely.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
  tags = local.tags
}

resource "aws_sns_topic" "reports" {
  name = "${local.name}-triage-reports"
  tags = local.tags
}

data "aws_iam_policy_document" "topic_access" {
  statement {
    effect  = "Allow"
    actions = ["SNS:Publish"]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    resources = [aws_sns_topic.alarms.arn, aws_sns_topic.reports.arn]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceOwner"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alarms" {
  arn    = aws_sns_topic.alarms.arn
  policy = data.aws_iam_policy_document.topic_access.json
}

resource "aws_sns_topic_policy" "reports" {
  arn    = aws_sns_topic.reports.arn
  policy = data.aws_iam_policy_document.topic_access.json
}

# AWS emails a confirmation link; delivery only starts once it is clicked.
resource "aws_sns_topic_subscription" "ops_email" {
  count     = var.ops_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.reports.arn
  protocol  = "email"
  endpoint  = var.ops_email
}

# ---------------------------------------------------------------------------
# Error detection
# ---------------------------------------------------------------------------

# Matching on the JSON `level` field rather than the literal "[ERROR]" string
# avoids false positives from user content: a note whose body contains
# "[ERROR]" would otherwise page the on-call and open a bogus report.
resource "aws_cloudwatch_log_metric_filter" "app_errors" {
  name           = "${local.name}-app-errors"
  log_group_name = var.log_group_name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name          = "AppErrorCount"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "app_errors" {
  alarm_name          = "${local.name}-app-errors"
  alarm_description   = "Application ERROR log lines exceeded the threshold; triage Lambda will debug and email a report."
  namespace           = local.metric_namespace
  metric_name         = "AppErrorCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.error_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name}-5xx"
  alarm_description   = "ALB returned 5xx responses to clients."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.alb_5xx_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { LoadBalancer = var.alb_arn_suffix }
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "latency" {
  alarm_name          = "${local.name}-latency-p95"
  alarm_description   = "p95 API latency degraded."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.latency_alarm_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { LoadBalancer = var.alb_arn_suffix }
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

# HealthyHostCount works without Container Insights, unlike ECS RunningTaskCount.
resource "aws_cloudwatch_metric_alarm" "no_healthy_hosts" {
  alarm_name          = "${local.name}-no-healthy-hosts"
  alarm_description   = "No healthy ECS tasks behind the load balancer."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }
  alarm_actions = [aws_sns_topic.reports.arn]
  tags          = local.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name}-rds-cpu"
  alarm_description   = "Sustained high CPU on the Postgres instance."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { DBInstanceIdentifier = var.db_instance_id }
  alarm_actions       = [aws_sns_topic.reports.arn]
  tags                = local.tags
}

# 2 GiB of the 20 GiB allocated.
resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${local.name}-rds-free-storage"
  alarm_description   = "Postgres free storage is running low."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2147483648
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { DBInstanceIdentifier = var.db_instance_id }
  alarm_actions       = [aws_sns_topic.reports.arn]
  tags                = local.tags
}

# Routed to the reports topic, never to the alarms topic: a failing triage
# function must not be able to trigger itself.
resource "aws_cloudwatch_metric_alarm" "triage_failures" {
  alarm_name          = "${local.name}-triage-failures"
  alarm_description   = "The error-triage Lambda itself is failing."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.triage.function_name }
  alarm_actions       = [aws_sns_topic.reports.arn]
  tags                = local.tags
}

# ---------------------------------------------------------------------------
# Deduplication state
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "dedup" {
  name         = "${local.name}-error-fingerprints"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "fingerprint"

  attribute {
    name = "fingerprint"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = local.tags
}

# ---------------------------------------------------------------------------
# LLM credentials
#
# Created empty on purpose. The real key is written out of band with
# `aws secretsmanager put-secret-value` so it never reaches a tfvars file,
# the state file, or version control; ignore_changes keeps Terraform from
# reverting it on the next apply.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "llm" {
  name                    = "${local.name}-llm-credentials"
  description             = "MiniMax API credentials for the error-triage Lambda."
  recovery_window_in_days = 0
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "llm_placeholder" {
  secret_id = aws_secretsmanager_secret.llm.id
  secret_string = jsonencode({
    api_key  = "REPLACE_ME"
    base_url = var.llm_base_url
    model    = var.llm_model
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ---------------------------------------------------------------------------
# Triage Lambda
# ---------------------------------------------------------------------------

resource "aws_iam_role" "triage" {
  name = "${local.name}-triage"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

data "aws_iam_policy_document" "triage" {
  statement {
    sid       = "OwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.triage.arn}:*"]
  }

  statement {
    sid       = "StartInsightsQuery"
    effect    = "Allow"
    actions   = ["logs:StartQuery"]
    resources = ["${var.log_group_arn}:*"]
  }

  # GetQueryResults and StopQuery are not resource-scopable in IAM.
  statement {
    sid       = "ReadInsightsResults"
    effect    = "Allow"
    actions   = ["logs:GetQueryResults", "logs:StopQuery"]
    resources = ["*"]
  }

  statement {
    sid       = "DedupState"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.dedup.arn]
  }

  statement {
    sid       = "ReadLlmSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.llm.arn]
  }

  statement {
    sid       = "PublishReport"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.reports.arn]
  }

  dynamic "statement" {
    for_each = var.ses_from == "" ? [] : [1]
    content {
      sid       = "SendEmailViaSes"
      effect    = "Allow"
      actions   = ["ses:SendEmail", "ses:SendRawEmail"]
      resources = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "triage" {
  name   = "${local.name}-triage"
  role   = aws_iam_role.triage.id
  policy = data.aws_iam_policy_document.triage.json
}

resource "aws_cloudwatch_log_group" "triage" {
  name              = "/aws/lambda/${local.name}-triage"
  retention_in_days = var.lambda_log_retention_days
  tags              = local.tags
}

resource "aws_lambda_function" "triage" {
  function_name    = "${local.name}-triage"
  role             = aws_iam_role.triage.arn
  handler          = "handler.main"
  runtime          = "python3.12"
  filename         = data.archive_file.triage.output_path
  source_code_hash = data.archive_file.triage.output_base64sha256
  # Logs Insights queries are the slow part; the function is otherwise idle.
  timeout     = 180
  memory_size = 512

  environment {
    variables = {
      CW_LOG_GROUP       = var.log_group_name
      ENVIRONMENT        = var.environment
      DEDUP_TABLE        = aws_dynamodb_table.dedup.name
      LLM_SECRET_ID      = aws_secretsmanager_secret.llm.arn
      LLM_BASE_URL       = var.llm_base_url
      LLM_MODEL          = var.llm_model
      SNS_TOPIC_ARN      = aws_sns_topic.reports.arn
      SES_FROM           = var.ses_from
      SES_TO             = var.ses_to
      LOOKBACK_MINUTES   = tostring(var.lookback_minutes)
      RESEND_EVERY       = tostring(var.resend_every)
      DEDUP_TTL_HOURS    = tostring(var.dedup_ttl_hours)
      MAX_EVENTS         = tostring(var.max_events)
      CODE_WINDOW_LINES  = tostring(var.code_window_lines)
      CODE_CONTEXT_CHARS = tostring(var.code_context_chars)
      DRY_RUN            = tostring(var.triage_dry_run)
    }
  }

  depends_on = [aws_iam_role_policy.triage, aws_cloudwatch_log_group.triage]
  tags       = local.tags
}

resource "aws_lambda_permission" "from_sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.triage.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alarms.arn
}

resource "aws_sns_topic_subscription" "triage" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.triage.arn
}

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name}-observability"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Application errors (log lines)"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            [local.metric_namespace, "AppErrorCount"]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "HTTP responses by class"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_2XX_Count", "LoadBalancer", var.alb_arn_suffix],
            [".", "HTTPCode_Target_4XX_Count", ".", "."],
            [".", "HTTPCode_Target_5XX_Count", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Latency (p50 / p95 / p99)"
          region = var.aws_region
          view   = "timeSeries"
          period = 300
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p50" }],
            ["...", { stat = "p95" }],
            ["...", { stat = "p99" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Database"
          region = var.aws_region
          view   = "timeSeries"
          period = 300
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.db_instance_id, { stat = "Average" }],
            [".", "DatabaseConnections", ".", ".", { stat = "Average" }],
          ]
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 12
        width  = 24
        height = 6
        properties = {
          title  = "Recent errors"
          region = var.aws_region
          query  = "SOURCE '${var.log_group_name}' | fields @timestamp, level, route, error_type, message | filter level = 'ERROR' | sort @timestamp desc | limit 50"
          view   = "table"
        }
      },
    ]
  })
}
