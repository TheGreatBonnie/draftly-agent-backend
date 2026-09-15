# AgentCore Runtime deployment for Draftly agents.

resource "aws_ecr_repository" "agentcore" {
  name                 = "draftly-agentcore"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_iam_role" "agentcore_runtime" {
  name = "${var.project_name}-${var.environment}-agentcore-runtime"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "bedrock-agentcore.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "agentcore_bedrock" {
  role       = aws_iam_role.agentcore_runtime.name
  policy_arn = aws_iam_policy.bedrock_access.arn
}

resource "aws_iam_role_policy" "agentcore_secrets" {
  name = "agentcore-secrets-read"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_secretsmanager_secret.database_url.arn,
          aws_secretsmanager_secret.provider_keys.arn,
        ]
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "agentcore" {
  name              = "/aws/agentcore/${var.project_name}-${var.environment}"
  retention_in_days = var.log_retention_days
}

resource "aws_security_group" "agentcore" {
  name        = "${var.project_name}-${var.environment}-agentcore"
  description = "Egress for Draftly AgentCore runtime"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# The AgentCore control-plane has no guaranteed first-class terraform provider
# resource; provision through the SDK (via null_resource) so the workflow is
# identical locally and from CI.
resource "null_resource" "deploy_agentcore_runtime" {
  count = var.agentcore_enabled ? 1 : 0

  triggers = {
    container_uri = var.agentcore_image_uri
    role_arn      = aws_iam_role.agentcore_runtime.arn
  }

  provisioner "local-exec" {
    command = <<-EOT
      python3 ${path.module}/../../../scripts/deploy_agentcore.py \
        --container-uri "${var.agentcore_image_uri}" \
        --role-arn "${aws_iam_role.agentcore_runtime.arn}" \
        --runtime-name draftly_agentcore \
        --region "${var.aws_region}" \
        --network-mode PUBLIC \
        --env "OTEL_EXPORTER_OTLP_ENDPOINT=${var.agentcore_otlp_endpoint}" \
        --env "OTEL_SERVICE_NAME=draftly-agentcore" \
        --out "${path.module}/agentcore-runtime.json"
    EOT
  }
}