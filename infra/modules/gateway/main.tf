# The Gateway, its Cedar policies, and its two targets, split by what they may write: the read tools' Lambda and
# block_card's (ADR-0004, Where the tools run).

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  # Policy engine and policy names allow only letters, digits, and underscores.
  policy_prefix = replace(var.prefix, "-", "_")
  reads_name    = "${var.prefix}-reads"
  block_name    = "${var.prefix}-block"
  # Generated from the contract by make build, and committed so a PR shows what the Gateway declares.
  targets = jsondecode(file("${path.module}/tools.json"))
  # A tool's action is named <target>___<tool>.
  tool_targets = merge([for target, tools in local.targets : { for tool in tools : tool.name => target }]...)
  functions = {
    reads = aws_lambda_function.reads.arn
    block = aws_lambda_function.block.arn
  }
}

resource "aws_bedrockagentcore_policy_engine" "this" {
  name = "${local.policy_prefix}_engine"
}

data "aws_iam_policy_document" "gateway_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name                 = "${var.prefix}-gateway"
  assume_role_policy   = data.aws_iam_policy_document.gateway_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

# Without the policy engine's actions, Cedar denies every call (spike S3).
data "aws_iam_policy_document" "gateway" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = values(local.functions)
  }
  statement {
    actions   = ["bedrock-agentcore:GetPolicyEngine"]
    resources = [aws_bedrockagentcore_policy_engine.this.policy_engine_arn]
  }
  statement {
    actions = [
      "bedrock-agentcore:AuthorizeAction",
      "bedrock-agentcore:PartiallyAuthorizeActions",
    ]
    resources = [
      aws_bedrockagentcore_policy_engine.this.policy_engine_arn,
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:gateway/*",
    ]
  }
}

resource "aws_iam_role_policy" "gateway" {
  name   = "gateway"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}

# exception_level stays unset: DEBUG would hand the caller the reason for a denial (decision 14).
resource "aws_bedrockagentcore_gateway" "this" {
  name            = var.prefix
  role_arn        = aws_iam_role.gateway.arn
  protocol_type   = "MCP"
  authorizer_type = "CUSTOM_JWT"

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = var.discovery_url
      allowed_clients = var.allowed_clients
    }
  }

  policy_engine_configuration {
    arn  = aws_bedrockagentcore_policy_engine.this.policy_engine_arn
    mode = "ENFORCE"
  }

  depends_on = [aws_iam_role_policy.gateway]
}

# Logs hold masked text only, and a KMS key would need a grant for every writer.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "reads" {
  name              = "/aws/lambda/${local.reads_name}"
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

resource "aws_iam_role" "reads" {
  name                 = local.reads_name
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

# Every read names its attributes, and is_fraud isn't among those allowed (ADR-0004's amendment of 2026-09-29).
data "aws_iam_policy_document" "reads" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.reads.arn}:*"]
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [var.tools_data_table_arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = var.tools_data_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }
  statement {
    actions   = ["dynamodb:Query"]
    resources = ["${var.tools_data_table_arn}/index/by_card"]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = var.tools_data_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [var.overlay_table.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = var.overlay_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [var.overlay_table.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = var.fault_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:ReturnValues"
      values   = ["NONE"]
    }
  }
}

resource "aws_iam_role_policy" "reads" {
  name   = "reads"
  role   = aws_iam_role.reads.id
  policy = data.aws_iam_policy_document.reads.json
}

# The execution record is the prototype's trace (ADR-0004, Operations).
#trivy:ignore:AVD-AWS-0066
resource "aws_lambda_function" "reads" {
  function_name    = local.reads_name
  role             = aws_iam_role.reads.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "faro.tools.reads.handler"
  filename         = var.tools_zip
  source_code_hash = filebase64sha256(var.tools_zip)
  memory_size      = 256
  timeout          = 10

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.reads.name
  }

  environment {
    variables = {
      TOOLS_DATA_TABLE = var.tools_data_table
      OVERLAY_TABLE    = var.overlay_table.name
    }
  }

  depends_on = [aws_iam_role_policy.reads]
}

# Logs hold masked text only, and a KMS key would need a grant for every writer.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "block" {
  name              = "/aws/lambda/${local.block_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "block" {
  name                 = local.block_name
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

# The only writer of the sandbox: it uses a confirmation up in one transaction with its first write, and counts the
# rest on it (ADR-0004, The confirmation). A transaction needs only its items' own actions.
data "aws_iam_policy_document" "block" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.block.arn}:*"]
  }
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [var.tools_data_table_arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = var.tools_data_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }
  # A card read by its key, a block's write, and a fault plan's count; never a query or is_fraud.
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [var.overlay_table.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = var.overlay_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }
  statement {
    actions   = ["dynamodb:PutItem"]
    resources = [var.overlay_table.arn]
  }
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [var.overlay_table.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = var.fault_attributes
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:ReturnValues"
      values   = ["NONE"]
    }
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [var.confirmations_table.arn]
  }
}

resource "aws_iam_role_policy" "block" {
  name   = "block"
  role   = aws_iam_role.block.id
  policy = data.aws_iam_policy_document.block.json
}

#trivy:ignore:AVD-AWS-0066
resource "aws_lambda_function" "block" {
  function_name    = local.block_name
  role             = aws_iam_role.block.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "faro.tools.block.handler"
  filename         = var.tools_zip
  source_code_hash = filebase64sha256(var.tools_zip)
  memory_size      = 256
  timeout          = 10

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.block.name
  }

  environment {
    variables = {
      TOOLS_DATA_TABLE    = var.tools_data_table
      OVERLAY_TABLE       = var.overlay_table.name
      CONFIRMATIONS_TABLE = var.confirmations_table.name
    }
  }

  depends_on = [aws_iam_role_policy.block]
}

moved {
  from = aws_bedrockagentcore_gateway_target.reads
  to   = aws_bedrockagentcore_gateway_target.this["reads"]
}

resource "aws_bedrockagentcore_gateway_target" "this" {
  for_each = local.targets

  name               = each.key
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = local.functions[each.key]
        tool_schema {
          dynamic "inline_payload" {
            for_each = each.value
            content {
              name        = inline_payload.value.name
              description = inline_payload.value.description
              input_schema {
                type = inline_payload.value.inputSchema.type
                dynamic "property" {
                  for_each = inline_payload.value.inputSchema.properties
                  content {
                    name        = property.key
                    type        = property.value.type
                    description = try(property.value.description, null)
                    required    = contains(inline_payload.value.inputSchema.required, property.key) ? true : null
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition = alltrue(flatten([
        for tool in each.value : [
          for property in values(tool.inputSchema.properties) : contains(["string", "integer", "number", "boolean"], property.type)
        ]
      ]))
      error_message = "A tool input has an object or array property, which this target doesn't declare yet."
    }
  }
}

# A policy names the Gateway and is validated against its targets, so it follows the target (spike S3).
resource "aws_bedrockagentcore_policy" "own_customer" {
  for_each = local.tool_targets

  name             = "${local.policy_prefix}_${each.key}"
  policy_engine_id = aws_bedrockagentcore_policy_engine.this.policy_engine_id

  definition {
    cedar {
      statement = <<-EOT
        permit (
          principal is AgentCore::OAuthUser,
          action == AgentCore::Action::"${each.value}___${each.key}",
          resource == AgentCore::Gateway::"${aws_bedrockagentcore_gateway.this.gateway_arn}"
        )
        when {
          principal.hasTag("customer_id") &&
          principal.getTag("customer_id") == context.input.customer_id &&
          principal.hasTag("origin_jti") &&
          principal.getTag("origin_jti") == context.input.origin_jti
        };
      EOT
    }
  }

  depends_on = [aws_bedrockagentcore_gateway_target.this]
}
