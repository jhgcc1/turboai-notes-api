terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket         = "turboai-notes-tfstate-615737882760"
    key            = "staging/terraform.tfstate"
    region         = "us-east-2"
    dynamodb_table = "turboai-notes-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" { default = "us-east-2" }
variable "db_password" { sensitive = true }
variable "django_secret_key" { sensitive = true }
variable "image_tag" { default = "latest" }

# Operator egress IP for MCP/SSH tunnel (WSL). Override via TF_VAR_bastion_ssh_cidr or terraform.tfvars when the IP changes.
variable "bastion_ssh_cidr" {
  type        = list(string)
  description = "CIDR blocks allowed to SSH to the bastion (port 22)."
  default     = ["187.123.69.5/32"]
}

variable "ops_email" {
  type        = string
  description = "Address that receives triage reports. Confirm the SNS subscription email after apply."
  default     = ""
}

# Optional Jira wiring. Keep jira_enabled=true so Deploy CI (no TF_VAR_jira_*)
# does not tear the secret down. Credentials live in Secrets Manager and are
# populated out of band — see docs/architecture/observability.md.
variable "jira_enabled" {
  type        = bool
  description = "When true, the triage Lambda creates a Jira issue per first sighting and links it in the email."
  # Staging wired 2026-08-10 to joaocavalcanti002.atlassian.net; project KAN.
  default     = true
}

variable "jira_project_key" {
  type        = string
  description = "Jira project key (e.g. \"KAN\"). Required when jira_enabled is true."
  default     = "KAN"
}

variable "jira_base_url" {
  type        = string
  description = "Public Jira site URL used in triage email browse links."
  default     = "https://joaocavalcanti002.atlassian.net"
}

variable "jira_issue_type" {
  type        = string
  description = "Jira issue type name (defaults to \"Bug\")."
  default     = "Bug"
}

# Tracking branch/PR on turboai-notes-api after Jira create. Credentials
# (PAT with repo scope) live in Secrets Manager — see observability.md.
variable "github_pr_enabled" {
  type        = bool
  description = "When true, triage Lambda opens fix/<JIRA-KEY> + draft PR on the API repo after Jira create."
  default     = true
}

variable "github_base_branch" {
  type        = string
  description = "Base branch for tracking PRs."
  default     = "develop"
}

module "stack" {
  source             = "../../modules/stack"
  environment        = "staging"
  aws_region         = var.aws_region
  db_password        = var.db_password
  django_secret_key  = var.django_secret_key
  image_tag          = var.image_tag
  bastion_public_key = trimspace(file("${path.module}/../../bastion.pub"))
  bastion_ssh_cidr   = var.bastion_ssh_cidr
}

module "observability" {
  source                  = "../../modules/observability"
  environment             = "staging"
  aws_region              = var.aws_region
  repo_root               = abspath("${path.module}/../../..")
  log_group_name          = module.stack.log_group
  log_group_arn           = module.stack.log_group_arn
  alb_arn_suffix          = module.stack.alb_arn_suffix
  target_group_arn_suffix = module.stack.target_group_arn_suffix
  ecs_cluster_name        = module.stack.ecs_cluster_name
  ecs_service_name        = module.stack.ecs_service_name
  db_instance_id          = module.stack.rds_instance_id
  ops_email               = var.ops_email
  jira_enabled            = var.jira_enabled
  jira_project_key        = var.jira_project_key
  jira_base_url           = var.jira_base_url
  jira_issue_type         = var.jira_issue_type
  github_pr_enabled       = var.github_pr_enabled
  github_base_branch      = var.github_base_branch
}

output "api_url" { value = module.stack.api_url }
output "web_url" { value = module.stack.web_url }
output "web_bucket" { value = module.stack.web_bucket }
output "cloudfront_id" { value = module.stack.cloudfront_id }
output "ecr_repository_url" { value = module.stack.ecr_repository_url }
output "rds_endpoint" { value = module.stack.rds_endpoint }
output "bastion_host" { value = module.stack.bastion_host }
output "bastion_instance_id" { value = module.stack.bastion_instance_id }
output "bastion_key_name" { value = module.stack.bastion_key_name }
output "log_group" { value = module.stack.log_group }
output "alarms_topic_arn" { value = module.observability.alarms_topic_arn }
output "reports_topic_arn" { value = module.observability.reports_topic_arn }
output "triage_function_name" { value = module.observability.triage_function_name }
output "llm_secret_arn" { value = module.observability.llm_secret_arn }
output "jira_secret_arn" { value = module.observability.jira_secret_arn }
output "github_secret_arn" { value = module.observability.github_secret_arn }
output "observability_dashboard" { value = module.observability.dashboard_name }
output "ecs_cluster_name" { value = module.stack.ecs_cluster_name }
output "ecs_service_name" { value = module.stack.ecs_service_name }
output "alb_dns_name" { value = module.stack.alb_dns_name }
