resource "aws_apigatewayv2_api" "main" {
  name          = "${var.project_name}-${var.environment}-api"
  protocol_type = "HTTP"
  description   = "Draftly API Gateway for webhook endpoints"

  cors_configuration {
    allow_credentials = true
    allow_headers     = ["*"]
    allow_methods     = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_origins     = ["*"]
    max_age           = 86400
  }
}

resource "aws_apigatewayv2_vpc_link" "main" {
  name               = "${var.project_name}-${var.environment}-vpc-link"
  security_group_ids = [aws_security_group.ecs_tasks.id]
  subnet_ids         = aws_subnet.private[*].id
}

resource "aws_apigatewayv2_integration" "api" {
  api_id           = aws_apigatewayv2_api.main.id
  integration_type = "HTTP_PROXY"
  integration_uri  = "https://${aws_lb.main.dns_name}"
  integration_method = "ANY"
  payload_format_version = "2.0"
  connection_type  = "VPC_LINK"
  connection_id    = aws_apigatewayv2_vpc_link.main.id
  timeout_milliseconds = 30000
}

resource "aws_apigatewayv2_route" "catch_all" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_stage" "main" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format          = jsonencode({
      requestId         = "$context.requestId"
      requestTime       = "$context.requestTime"
      httpMethod        = "$context.httpMethod"
      path              = "$context.path"
      status            = "$context.status"
      responseLength    = "$context.responseLength"
      integrationLatency = "$context.integrationLatency"
    })
  }
}

resource "aws_apigatewayv2_stage" "dev" {
  count       = var.environment == "dev" ? 1 : 0
  api_id      = aws_apigatewayv2_api.main.id
  name        = "dev"
  auto_deploy = true
}

resource "aws_wafv2_web_acl" "api_gateway" {
  count       = var.enable_waf ? 1 : 0
  name        = "${var.project_name}-${var.environment}-waf"
  scope       = "REGIONAL"
  description = "WAF for API Gateway"

  default_action {
    allow {}
  }

  rule {
    name     = "RateLimit"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "RateLimit"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "APIWAF"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "api_gateway" {
  count              = var.enable_waf ? 1 : 0
  resource_arn       = aws_apigatewayv2_stage.main.arn
  web_acl_arn        = aws_wafv2_web_acl.api_gateway.arn
}

resource "aws_apigatewayv2_domain_name" "custom" {
  count = length(var.custom_domain_name) > 0 ? 1 : 0
  domain_name = var.custom_domain_name
  domain_name_configuration {
    certificate_arn = aws_acm_certificate.custom.arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

resource "aws_apigatewayv2_api_mapping" "custom" {
  count      = length(var.custom_domain_name) > 0 ? 1 : 0
  api_id     = aws_apigatewayv2_api.main.id
  domain_name = var.custom_domain_name
  stage      = aws_apigatewayv2_stage.main.name
}

variable "custom_domain_name" {
  description = "Custom domain name for API Gateway (optional)"
  type        = string
  default     = ""
}

resource "aws_acm_certificate" "custom" {
  count = length(var.custom_domain_name) > 0 ? 1 : 0
  domain_name       = var.custom_domain_name
  validation_method = "DNS"
}