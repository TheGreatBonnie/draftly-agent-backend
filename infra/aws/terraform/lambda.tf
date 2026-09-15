resource "aws_lambda_function" "indexing_scheduler" {
  filename         = "../lambdas/indexing_scheduler.zip"
  function_name    = "${var.project_name}-${var.environment}-indexing-scheduler"
  role             = aws_iam_role.lambda.arn
  handler          = "indexing_scheduler.handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 256
  source_code_hash = filebase64sha256("../lambdas/indexing_scheduler.zip")

  environment {
    variables = {
      CLUSTER_NAME        = aws_ecs_cluster.main.name
      TASK_DEFINITION     = aws_ecs_task_definition.worker.arn
      SUBNET_IDS          = join(",", aws_subnet.private[*].id)
      SECURITY_GROUP_ID   = aws_security_group.ecs_tasks.id
      CONTAINER_NAME      = "worker"
      WORKER_TYPE         = "indexing"
    }
  }
}

resource "aws_cloudwatch_event_rule" "indexing_schedule" {
  name                = "${var.project_name}-${var.environment}-indexing-schedule"
  description         = "Daily trigger for documentation indexing"
  schedule_expression = "cron(0 3 * * ? *)"
  is_enabled          = true
}

resource "aws_cloudwatch_event_target" "indexing_schedule" {
  rule      = aws_cloudwatch_event_rule.indexing_schedule.name
  arn       = aws_lambda_function.indexing_scheduler.arn
  role_arn  = aws_iam_role.eventbridge_lambda.arn
  input     = jsonencode({ worker_type = "indexing" })
}

resource "aws_lambda_permission" "allow_eventbridge_indexing" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.indexing_scheduler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.indexing_schedule.arn
}

resource "aws_lambda_function" "evaluation_scheduler" {
  filename         = "../lambdas/evaluation_scheduler.zip"
  function_name    = "${var.project_name}-${var.environment}-evaluation-scheduler"
  role             = aws_iam_role.lambda.arn
  handler          = "evaluation_scheduler.handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 256
  source_code_hash = filebase64sha256("../lambdas/evaluation_scheduler.zip")

  environment {
    variables = {
      CLUSTER_NAME        = aws_ecs_cluster.main.name
      TASK_DEFINITION     = aws_ecs_task_definition.worker.arn
      SUBNET_IDS          = join(",", aws_subnet.private[*].id)
      SECURITY_GROUP_ID   = aws_security_group.ecs_tasks.id
      CONTAINER_NAME      = "worker"
      WORKER_TYPE         = "evaluation"
    }
  }
}

resource "aws_cloudwatch_event_rule" "evaluation_schedule" {
  name                = "${var.project_name}-${var.environment}-evaluation-schedule"
  description         = "Hourly trigger for evaluation worker (watch mode)"
  schedule_expression = "cron(0 * * * ? *)"
  is_enabled          = true
}

resource "aws_cloudwatch_event_target" "evaluation_schedule" {
  rule      = aws_cloudwatch_event_rule.evaluation_schedule.name
  arn       = aws_lambda_function.evaluation_scheduler.arn
  role_arn  = aws_iam_role.eventbridge_lambda.arn
  input     = jsonencode({ worker_type = "evaluation" })
}

resource "aws_lambda_permission" "allow_eventbridge_evaluation" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.evaluation_scheduler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.evaluation_schedule.arn
}

resource "aws_iam_role" "lambda" {
  name = "${var.project_name}-${var.environment}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "lambda_ecs" {
  name = "${var.project_name}-${var.environment}-lambda-ecs-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ecs:RunTask",
        "ecs:DescribeTasks"
      ]
      Resource = [
        aws_ecs_task_definition.worker.arn,
        "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${aws_ecs_cluster.main.name}",
        "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*"
      ]
      Condition = {
        StringEquals = {
          "ecs:cluster" = aws_ecs_cluster.main.arn
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_ecs" {
  role       = aws_iam_role.lambda.name
  policy_arn = aws_iam_policy.lambda_ecs.arn
}

resource "aws_iam_role" "eventbridge_lambda" {
  name = "${var.project_name}-${var.environment}-eventbridge-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "events.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_policy" "eventbridge_lambda" {
  name = "${var.project_name}-${var.environment}-eventbridge-lambda-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "lambda:InvokeFunction"
      ]
      Resource = [
        aws_lambda_function.indexing_scheduler.arn,
        aws_lambda_function.evaluation_scheduler.arn
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eventbridge_lambda" {
  role       = aws_iam_role.eventbridge_lambda.name
  policy_arn = aws_iam_policy.eventbridge_lambda.arn
}