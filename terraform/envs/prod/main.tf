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
    key            = "prod/terraform.tfstate"
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

module "stack" {
  source             = "../../modules/stack"
  environment        = "prod"
  aws_region         = var.aws_region
  db_password        = var.db_password
  django_secret_key  = var.django_secret_key
  image_tag          = var.image_tag
  bastion_public_key = trimspace(file("${path.module}/../../bastion.pub"))
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
output "ecs_cluster_name" { value = module.stack.ecs_cluster_name }
output "ecs_service_name" { value = module.stack.ecs_service_name }
output "alb_dns_name" { value = module.stack.alb_dns_name }
