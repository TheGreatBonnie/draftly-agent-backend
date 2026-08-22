resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.secrets_manager_prefix}/database-url"
  description             = "PostgreSQL connection string for Draftly"
  rotation_lambda_arn     = ""
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = 30

  rotation_rules {
    automatically_after_days = 90
  }
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = var.database_url
}

resource "aws_secretsmanager_secret" "bedrock_keys" {
  name                    = "${var.secrets_manager_prefix}/bedrock-keys"
  description             = "AWS credentials for Bedrock (if not using IAM role)"
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "bedrock_keys" {
  secret_id     = aws_secretsmanager_secret.bedrock_keys.id
  secret_string = jsonencode({
    AWS_ACCESS_KEY_ID     = var.bedrock_access_key
    AWS_SECRET_ACCESS_KEY = var.bedrock_secret_key
    AWS_SESSION_TOKEN     = var.bedrock_session_token
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "provider_keys" {
  name                    = "${var.secrets_manager_prefix}/provider-keys"
  description             = "API keys for external model providers"
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "provider_keys" {
  secret_id     = aws_secretsmanager_secret.provider_keys.id
  secret_string = jsonencode({
    NVIDIA_API_KEY      = var.nvidia_api_key
    NVIDIA_BASE_URL     = var.nvidia_base_url
    REQUESTY_API_KEY    = var.requesty_api_key
    REQUESTY_BASE_URL   = var.requesty_base_url
    ORCAROUTER_API_KEY  = var.orcarouter_api_key
    ORCAROUTER_BASE_URL = var.orcarouter_base_url
    OPENROUTER_API_KEY  = var.openrouter_api_key
    OPENROUTER_BASE_URL = var.openrouter_base_url
    MANTLE_API_KEY      = var.mantle_api_key
    MANTLE_ENDPOINT_URL = var.mantle_endpoint_url
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "github_app" {
  name                    = "${var.secrets_manager_prefix}/github-app"
  description             = "GitHub App credentials"
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "github_app" {
  secret_id     = aws_secretsmanager_secret.github_app.id
  secret_string = jsonencode({
    GITHUB_APP_ID          = var.github_app_id
    GITHUB_APP_PRIVATE_KEY = var.github_app_private_key
    GITHUB_WEBHOOK_SECRET  = var.github_webhook_secret
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "slack_app" {
  name                    = "${var.secrets_manager_prefix}/slack-app"
  description             = "Slack App credentials"
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "slack_app" {
  secret_id     = aws_secretsmanager_secret.slack_app.id
  secret_string = jsonencode({
    SLACK_CLIENT_ID     = var.slack_client_id
    SLACK_CLIENT_SECRET = var.slack_client_secret
    SLACK_SIGNING_SECRET = var.slack_signing_secret
    SLACK_BOT_TOKEN     = var.slack_bot_token
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "discord_app" {
  name                    = "${var.secrets_manager_prefix}/discord-app"
  description             = "Discord App credentials"
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "discord_app" {
  secret_id     = aws_secretsmanager_secret.discord_app.id
  secret_string = jsonencode({
    DISCORD_CLIENT_ID     = var.discord_client_id
    DISCORD_CLIENT_SECRET = var.discord_client_secret
    DISCORD_PUBLIC_KEY    = var.discord_public_key
    DISCORD_BOT_TOKEN     = var.discord_bot_token
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "clerk" {
  name                    = "${var.secrets_manager_prefix}/clerk"
  description             = "Clerk authentication credentials"
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "clerk" {
  secret_id     = aws_secretsmanager_secret.clerk.id
  secret_string = jsonencode({
    CLERK_SECRET_KEY       = var.clerk_secret_key
    CLERK_PUBLISHABLE_KEY  = var.clerk_publishable_key
    CLERK_WEBHOOK_SECRET   = var.clerk_webhook_secret
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_kms_key" "secrets" {
  description             = "KMS key for encrypting secrets"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Allow root account full access"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow ECS tasks to decrypt secrets"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.api_task.arn
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = "*"
      },
      {
        Sid    = "Allow ECS worker tasks to decrypt secrets"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.worker_task.arn
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = "*"
      },
      {
        Sid    = "Allow Lambda to decrypt secrets"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.lambda.arn
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${var.project_name}-${var.environment}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

variable "database_url" {
  description = "PostgreSQL connection string (set via TF_VAR or SSM)"
  type        = string
  sensitive   = true
}

variable "bedrock_access_key" {
  description = "Bedrock AWS access key (optional if using IAM role)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "bedrock_secret_key" {
  description = "Bedrock AWS secret key"
  type        = string
  default     = ""
  sensitive   = true
}

variable "bedrock_session_token" {
  description = "Bedrock AWS session token"
  type        = string
  default     = ""
  sensitive   = true
}

variable "nvidia_api_key" { type = string; default = ""; sensitive = true }
variable "nvidia_base_url" { type = string; default = ""; sensitive = true }
variable "requesty_api_key" { type = string; default = ""; sensitive = true }
variable "requesty_base_url" { type = string; default = ""; sensitive = true }
variable "orcarouter_api_key" { type = string; default = ""; sensitive = true }
variable "orcarouter_base_url" { type = string; default = ""; sensitive = true }
variable "openrouter_api_key" { type = string; default = ""; sensitive = true }
variable "openrouter_base_url" { type = string; default = ""; sensitive = true }
variable "mantle_api_key" { type = string; default = ""; sensitive = true }
variable "mantle_endpoint_url" { type = string; default = ""; sensitive = true }
variable "github_app_id" { type = string; default = ""; sensitive = true }
variable "github_app_private_key" { type = string; default = ""; sensitive = true }
variable "github_webhook_secret" { type = string; default = ""; sensitive = true }
variable "slack_client_id" { type = string; default = ""; sensitive = true }
variable "slack_client_secret" { type = string; default = ""; sensitive = true }
variable "slack_signing_secret" { type = string; default = ""; sensitive = true }
variable "slack_bot_token" { type = string; default = ""; sensitive = true }
variable "discord_client_id" { type = string; default = ""; sensitive = true }
variable "discord_client_secret" { type = string; default = ""; sensitive = true }
variable "discord_public_key" { type = string; default = ""; sensitive = true }
variable "discord_bot_token" { type = string; default = ""; sensitive = true }
variable "clerk_secret_key" { type = string; default = ""; sensitive = true }
variable "clerk_publishable_key" { type = string; default = ""; sensitive = true }
variable "clerk_webhook_secret" { type = string; default = ""; sensitive = true }