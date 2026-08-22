variable "project_name" {
  description = "Project name prefix for all resources"
  type        = string
  default     = "draftly"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (2 AZs minimum)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (2 AZs minimum)"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24"]
}

variable "db_subnet_cidrs" {
  description = "CIDR blocks for database subnets (2 AZs minimum)"
  type        = list(string)
  default     = ["10.0.21.0/24", "10.0.22.0/24"]
}

variable "api_image_uri" {
  description = "ECR image URI for the API/event-worker container"
  type        = string
  default     = ""
}

variable "worker_image_uri" {
  description = "ECR image URI for the background worker container"
  type        = string
  default     = ""
}

variable "api_desired_count" {
  description = "Desired count for the API service"
  type        = number
  default     = 2
}

variable "worker_desired_counts" {
  description = "Desired counts for each worker service"
  type        = map(number)
  default     = {
    workflow  = 1
    indexing  = 1
    evaluation = 0
  }
}

variable "api_cpu" {
  description = "CPU units for API task (1024 = 1 vCPU)"
  type        = number
  default     = 1024
}

variable "api_memory" {
  description = "Memory in MiB for API task"
  type        = number
  default     = 2048
}

variable "worker_cpu" {
  description = "CPU units for worker tasks"
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Memory in MiB for worker tasks"
  type        = number
  default     = 2048
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

variable "db_allocated_storage" {
  description = "Allocated storage in GB"
  type        = number
  default     = 100
}

variable "db_max_allocated_storage" {
  description = "Max allocated storage for autoscaling"
  type        = number
  default     = 500
}

variable "secrets_manager_prefix" {
  description = "Prefix for Secrets Manager secret names"
  type        = string
  default     = "draftly/prod"
}

variable "ecr_repository_name" {
  description = "ECR repository name"
  type        = string
  default     = "draftly"
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "alb_access_logs_bucket" {
  description = "S3 bucket for ALB access logs (optional)"
  type        = string
  default     = ""
}

variable "enable_waf" {
  description = "Enable WAF on the API Gateway"
  type        = bool
  default     = true
}

variable "maintenance_window" {
  description = "Maintenance window for RDS (day:hour:minute)"
  type        = string
  default     = "sun:04:00-sun:05:00"
}