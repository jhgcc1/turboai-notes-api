variable "project" {
  type    = string
  default = "turboai-notes"
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "log_group_name" {
  type        = string
  description = "Application log group produced by the stack module."
}

variable "log_group_arn" {
  type = string
}

variable "alb_arn_suffix" {
  type        = string
  description = "ALB arn_suffix, for AWS/ApplicationELB metric dimensions."
}

variable "target_group_arn_suffix" {
  type = string
}

variable "ecs_cluster_name" {
  type = string
}

variable "ecs_service_name" {
  type = string
}

variable "db_instance_id" {
  type = string
}

variable "repo_root" {
  type        = string
  description = "Repository root, used to bundle apps/ and config/ into the Lambda zip."
}

variable "ops_email" {
  type        = string
  description = <<-EOT
    Address subscribed to the ops SNS topic; triage reports are delivered here.
    Empty creates the topic without a subscriber (no email is sent).
    AWS sends a confirmation link that must be clicked before delivery starts.
  EOT
  default     = ""
}

variable "ses_from" {
  type        = string
  description = "Optional verified SES sender. Empty keeps delivery on SNS."
  default     = ""
}

variable "ses_to" {
  type        = string
  description = "Optional comma-separated SES recipients. Requires ses_from."
  default     = ""
}

variable "llm_base_url" {
  type    = string
  default = "https://api.minimax.io/v1"
}

variable "llm_model" {
  type    = string
  default = "MiniMax-M2.1"
}

variable "error_alarm_threshold" {
  type        = number
  description = "ERROR log lines in a 5-minute window before triage fires."
  default     = 5
}

variable "alb_5xx_threshold" {
  type    = number
  default = 10
}

variable "latency_alarm_threshold_seconds" {
  type    = number
  default = 2
}

variable "lookback_minutes" {
  type        = number
  description = "How far back the Lambda samples ERROR logs when an alarm fires."
  default     = 15
}

variable "resend_every" {
  type        = number
  description = "After the first report, re-notify every N occurrences of a fingerprint."
  default     = 10
}

variable "dedup_ttl_hours" {
  type        = number
  description = "How long a fingerprint stays suppressed before counting as new again."
  default     = 72
}

variable "max_events" {
  type        = number
  description = "Maximum number of ERROR log lines fed to the LLM per triage invocation."
  default     = 25
}

variable "code_window_lines" {
  type        = number
  description = "Lines of context fetched above and below each traceback frame."
  default     = 40
}

variable "code_context_chars" {
  type        = number
  description = "Hard cap on the total size of source code shipped to the LLM."
  default     = 24000
}

variable "triage_dry_run" {
  type        = bool
  description = "When true the Lambda analyses errors but sends no email."
  default     = false
}

variable "jira_enabled" {
  type        = bool
  description = "When true, the triage Lambda creates a Jira issue for every first sighting of a fingerprint and links it in the email. The actual credentials live in a separate Secrets Manager secret populated out of band."
  default     = false
}

variable "jira_project_key" {
  type        = string
  description = "Jira project key (e.g. \"KAN\") the Lambda files issues under. Required when jira_enabled is true; ignored otherwise."
  default     = ""
}

variable "jira_base_url" {
  type        = string
  description = "Optional public Jira site URL for browse links in email (e.g. https://example.atlassian.net). When empty the Lambda falls back to base_url from the credentials secret."
  default     = ""
}

variable "jira_issue_type" {
  type        = string
  description = "Jira issue type name (e.g. \"Bug\", \"Task\"). Created issues use this type."
  default     = "Bug"
}

variable "jira_secret_id_override" {
  type        = string
  description = "Optional explicit Secrets Manager secret id (name or ARN) holding the Jira credentials. When empty, the module generates a placeholder secret named after the project / environment. The Lambda reads it with secretsmanager:GetSecretValue."
  default     = ""
}

variable "github_pr_enabled" {
  type        = bool
  description = "When true, after a successful Jira create the triage Lambda opens a tracking branch (and draft PR) on turboai-notes-api named fix/<JIRA-KEY>. Credentials live in a separate Secrets Manager secret populated out of band. Web repo is out of scope."
  default     = false
}

variable "github_base_branch" {
  type        = string
  description = "Base branch for tracking PRs (staging workflow: develop)."
  default     = "develop"
}

variable "github_secret_id_override" {
  type        = string
  description = "Optional explicit Secrets Manager secret id (name or ARN) holding GitHub credentials JSON {token, owner, repo}. When empty and github_pr_enabled, the module creates a placeholder secret."
  default     = ""
}

variable "lambda_log_retention_days" {
  type    = number
  default = 14
}

variable "tags" {
  type    = map(string)
  default = {}
}
