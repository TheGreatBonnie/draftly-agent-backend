# IAM policies for Draftly services

resource "aws_iam_policy" "bedrock_access" {
  name        = "${var.project_name}-bedrock-access"
  description = "Bedrock model invocation permissions for Draftly agents"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:GetFoundationModel",
          "bedrock:ListFoundationModels",
        ]
        Resource = [
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude*",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.nova*",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/meta.llama*",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/cohere*",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan*",
        ]
      },
      # For prompt caching, guardrails, token counting
      {
        Effect   = "Allow"
        Action   = [
          "bedrock:CreateGuardrail",
          "bedrock:GetGuardrail",
          "bedrock:UpdateGuardrail",
          "bedrock:DeleteGuardrail",
          "bedrock:PutModelInvocationLoggingConfiguration",
          "bedrock:GetModelInvocationLoggingConfiguration",
        ]
        Resource = "*"
      },
      # For CountTokens API (native token counting)
      {
        Effect   = "Allow"
        Action   = [
          "bedrock:CountTokens",
        ]
        Resource = [
          "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude*",
          "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.nova*",
        ]
      },
    ]
  })
}

resource "aws_iam_role" "draftly_task_role" {
  name = "${var.project_name}-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_access" {
  role       = aws_iam_role.draftly_task_role.name
  policy_arn = aws_iam_policy.bedrock_access.arn
}

# Additional managed policies for ECS task execution
resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.draftly_task_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy_attachment" "cloudwatch_logs" {
  role       = aws_iam_role.draftly_task_role.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
}