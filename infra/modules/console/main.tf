# The console API (ADR-0007, The console API, and its amendments of 2026-09-30): an HTTP API behind a JWT authorizer
# that accepts the staff app client's access tokens only, served on the site's own origin under /api, with a reading
# Lambda per route, each allowed only the reads its route needs.

locals {
  functions = {
    queue = {
      name    = "${var.prefix}-console-queue"
      route   = "GET /api/cases"
      path    = "GET/api/cases"
      handler = "faro.console.handlers.queue_handler"
    }
    case = {
      name    = "${var.prefix}-console-case"
      route   = "GET /api/cases/{reference}"
      path    = "GET/api/cases/*"
      handler = "faro.console.handlers.case_handler"
    }
  }
  # faro.console.queue.PROJECTED: the index's keys and projection, so a page never reads a payload.
  queue_attributes = [
    "pk", "queue_key", "queue_order", "reference", "priority", "reason_code", "language", "filed_at", "flagged",
  ]
  # faro.console.case.CALL: a tool call's attributes but its input, so the console never reads a message
  # (input), a reply (text), or a model's output (output) (CTL-05).
  call_attributes = [
    "sign_in", "entry_key", "kind", "call_id", "tool", "via", "attempt", "called_at", "latency_ms", "request_id",
    "outcome", "result", "error",
  ]
  # Access tokens carry it and ID tokens don't, so an ID token, whose aud would name the staff client too, stops here.
  scopes = ["aws.cognito.signin.user.admin"]
}

# Logs hold references and request IDs only, and a KMS key would need a grant for every writer.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "function" {
  for_each = local.functions

  name              = "/aws/lambda/${each.value.name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "function" {
  for_each = local.functions

  name                 = each.value.name
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

# The queue index alone, naming its projection, and the demo's partitions only (EVL-13): IAM applies LeadingKeys to
# the index's key (ADR-0004), and the code builds every key from demo besides.
data "aws_iam_policy_document" "queue" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.function["queue"].arn}:*"]
  }
  statement {
    actions   = ["dynamodb:Query"]
    resources = ["${var.cases_table.arn}/index/by_queue"]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = local.queue_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["demo#*"]
    }
  }
}

# A case and its reference, and of the execution records a tool call's attributes only.
data "aws_iam_policy_document" "case" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.function["case"].arn}:*"]
  }
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [var.cases_table.arn]
  }
  statement {
    actions   = ["dynamodb:Query"]
    resources = [var.execution_records_table.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = local.call_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }
}

resource "aws_iam_role_policy" "function" {
  for_each = local.functions

  name   = "console-${each.key}"
  role   = aws_iam_role.function[each.key].id
  policy = { queue = data.aws_iam_policy_document.queue.json, case = data.aws_iam_policy_document.case.json }[each.key]
}

# The execution record is the prototype's trace (ADR-0004, Operations).
#trivy:ignore:AVD-AWS-0066
resource "aws_lambda_function" "function" {
  for_each = local.functions

  function_name    = each.value.name
  role             = aws_iam_role.function[each.key].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = each.value.handler
  filename         = var.tools_zip
  source_code_hash = filebase64sha256(var.tools_zip)
  memory_size      = 256
  timeout          = 10

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.function[each.key].name
  }

  environment {
    variables = {
      CASES_TABLE             = var.cases_table.name
      EXECUTION_RECORDS_TABLE = var.execution_records_table.name
      STAFF_CLIENT_ID         = var.staff_client_id
    }
  }

  depends_on = [aws_iam_role_policy.function]
}

resource "aws_apigatewayv2_api" "console" {
  name          = "${var.prefix}-console"
  protocol_type = "HTTP"
  description   = "The human agents' console API, served on the site's origin under /api (ADR-0007)"
}

resource "aws_apigatewayv2_authorizer" "staff" {
  api_id           = aws_apigatewayv2_api.console.id
  name             = "staff"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  # Checked against client_id, which Cognito's access tokens carry in place of aud (ADR-0004, To verify on the first
  # deploy).
  jwt_configuration {
    issuer   = var.issuer
    audience = [var.staff_client_id]
  }
}

resource "aws_apigatewayv2_integration" "function" {
  for_each = local.functions

  api_id                 = aws_apigatewayv2_api.console.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.function[each.key].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "function" {
  for_each = local.functions

  api_id               = aws_apigatewayv2_api.console.id
  route_key            = each.value.route
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.staff.id
  authorization_scopes = local.scopes
  target               = "integrations/${aws_apigatewayv2_integration.function[each.key].id}"
}

resource "aws_lambda_permission" "api" {
  for_each = local.functions

  statement_id  = "AllowConsoleApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.function[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.console.execution_arn}/*/${each.value.path}"
}

# Named under /aws/vendedlogs/ so CloudWatch Logs manages the delivery's resource policy. Every field but the IP, the
# user agent, the path, and the claims, so no reference or person is logged.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/vendedlogs/apigateway/${var.prefix}-console"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.console.id
  name        = "$default"
  auto_deploy = true

  # Caps a runaway tab or script, well above what the consoles' polling needs (ADR-0007, Freshness; OPS-08).
  default_route_settings {
    throttling_rate_limit  = var.rate_limit.rate
    throttling_burst_limit = var.rate_limit.burst
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId         = "$context.requestId"
      requestTime       = "$context.requestTime"
      routeKey          = "$context.routeKey"
      status            = "$context.status"
      responseLatency   = "$context.responseLatency"
      integrationStatus = "$context.integrationStatus"
      integrationError  = "$context.integrationErrorMessage"
      authorizerError   = "$context.authorizer.error"
    })
  }
}
