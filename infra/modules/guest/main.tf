# The guest entry (ADR-0007, The guest entry, and its amendment of 2026-11-08): POST /api/guest on the console API with
# no authorizer, a Lambda that makes a visitor for each click, and a Lambda that deletes visitors a day after they were
# made. Each role holds what its code calls, and neither may write Parameter Store, so neither can write the staff
# allowlist.

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  # faro.guest.customers.parameter: make guest-list writes it, so Terraform holds the path and the read alone.
  guest_list = "/${var.prefix}/guest-entry/customers"
  functions = {
    entry = {
      name    = "${var.prefix}-guest-entry"
      handler = "faro.guest.entry.handler"
      timeout = 15
      environment = {
        USER_POOL_ID         = var.user_pool.id
        CUSTOMER_CLIENT_ID   = var.customer_client_id
        USAGE_COUNTERS_TABLE = var.usage_counters_table.name
        GUEST_LIST_PARAMETER = local.guest_list
        GATEWAY_URL          = var.gateway_url
        LIST_CARDS_TARGET    = var.list_cards_target
      }
    }
    cleanup = {
      name        = "${var.prefix}-guest-cleanup"
      handler     = "faro.guest.cleanup.handler"
      timeout     = 120
      environment = { USER_POOL_ID = var.user_pool.id }
    }
  }
}

# Logs hold outcomes and counts only, and a KMS key would need a grant for every writer.
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

# Cognito's administrator actions hold to the pool, never to a group, so which groups a visitor gets rests on the code;
# the trigger keeps any user it makes from a staff token (ADR-0007, Sign-in).
data "aws_iam_policy_document" "entry" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.function["entry"].arn}:*"]
  }
  statement {
    actions = [
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminAddUserToGroup",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:AdminInitiateAuth",
    ]
    resources = [var.user_pool.arn]
  }
  # The cap's turns, for the pause, and its guests.
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [var.usage_counters_table.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["cap"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["counter", "window", "turns", "guests"]
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }
  # Counts the day's guests on a condition and reads no count back.
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [var.usage_counters_table.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["guests"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["counter", "window", "guests", "expires_at"]
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:ReturnValues"
      values   = ["NONE"]
    }
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${local.guest_list}"]
  }
}

# Lists the visitor group and deletes; its code deletes only what it listed.
data "aws_iam_policy_document" "cleanup" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.function["cleanup"].arn}:*"]
  }
  statement {
    actions   = ["cognito-idp:ListUsersInGroup", "cognito-idp:AdminDeleteUser"]
    resources = [var.user_pool.arn]
  }
}

resource "aws_iam_role_policy" "function" {
  for_each = local.functions

  name   = "guest-${each.key}"
  role   = aws_iam_role.function[each.key].id
  policy = { entry = data.aws_iam_policy_document.entry.json, cleanup = data.aws_iam_policy_document.cleanup.json }[each.key]
}

# The execution record is the prototype's trace (ADR-0004, Operations).
#trivy:ignore:AVD-AWS-0066
resource "aws_lambda_function" "function" {
  for_each = local.functions

  function_name                  = each.value.name
  role                           = aws_iam_role.function[each.key].arn
  runtime                        = "python3.13"
  architectures                  = ["arm64"]
  handler                        = each.value.handler
  filename                       = var.tools_zip
  source_code_hash               = filebase64sha256(var.tools_zip)
  memory_size                    = 256
  timeout                        = each.value.timeout
  reserved_concurrent_executions = each.key == "entry" ? var.concurrency : 1

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.function[each.key].name
  }

  environment {
    variables = each.value.environment
  }

  depends_on = [aws_iam_role_policy.function]
}

resource "aws_apigatewayv2_integration" "entry" {
  api_id                 = var.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.function["entry"].invoke_arn
  payload_format_version = "2.0"
}

# No authorizer: a stranger has no token yet. The stage throttles the route on its own (the console module).
resource "aws_apigatewayv2_route" "entry" {
  api_id             = var.api.id
  route_key          = "POST /api/guest"
  authorization_type = "NONE"
  target             = "integrations/${aws_apigatewayv2_integration.entry.id}"
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowConsoleApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.function["entry"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api.execution_arn}/*/POST/api/guest"
}

resource "aws_cloudwatch_event_rule" "cleanup" {
  name                = "${var.prefix}-guest-cleanup"
  description         = "Deletes the visitors made more than a day before (ADR-0007, The guest entry)"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "cleanup" {
  rule = aws_cloudwatch_event_rule.cleanup.name
  arn  = aws_lambda_function.function["cleanup"].arn
}

resource "aws_lambda_permission" "schedule" {
  statement_id  = "AllowSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.function["cleanup"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cleanup.arn
}
