# Cognito and its pre-token trigger (ADR-0004, ADR-0007).

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  pre_token_name = "${var.prefix}-pre-token"
  # Built rather than referenced: the trigger's environment holds the clients' IDs, and the clients need the pool.
  pre_token_arn = "arn:aws:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${local.pre_token_name}"

  groups = {
    customer    = "Customers: the chat, through the customers' client"
    human_agent = "Human agents: the handoff console, through the staff client"
    ai_team     = "The AI team: the evaluation report, through the staff client"
    evaluation  = "The evaluation's test users, who are customers too"
  }
}

resource "aws_cognito_user_pool" "this" {
  name                = var.prefix
  user_pool_tier      = "ESSENTIALS"
  deletion_protection = "INACTIVE"

  # The clients' IDs are public in the site's configuration, so self sign-up would hand anyone a token.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  schema {
    name                = "customer_id"
    attribute_data_type = "String"
    mutable             = true
    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }

  lambda_config {
    pre_token_generation_config {
      lambda_arn     = local.pre_token_arn
      lambda_version = "V2_0"
    }
  }
}

resource "aws_cognito_user_group" "this" {
  for_each = local.groups

  name         = each.key
  description  = each.value
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_user_pool_client" "this" {
  for_each = toset(["customers", "staff"])

  name            = "${var.prefix}-${each.key}"
  user_pool_id    = aws_cognito_user_pool.this.id
  generate_secret = false
  # The browser signs in with SRP; scripts and tests sign in through IAM.
  explicit_auth_flows           = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_ADMIN_USER_PASSWORD_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  # Left unset, a user could rewrite custom:customer_id with their own access token (spike S3).
  write_attributes = ["locale"]

  access_token_validity  = 15
  id_token_validity      = 15
  refresh_token_validity = 60
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "minutes"
  }
}

# Logs hold masked text only, and a KMS key would need a grant for every writer.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "pre_token" {
  name              = "/aws/lambda/${local.pre_token_name}"
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

resource "aws_iam_role" "pre_token" {
  name                 = local.pre_token_name
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "pre_token" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.pre_token.arn}:*"]
  }
}

resource "aws_iam_role_policy" "pre_token" {
  name   = "logs"
  role   = aws_iam_role.pre_token.id
  policy = data.aws_iam_policy_document.pre_token.json
}

# The execution record is the prototype's trace (ADR-0004, Operations).
#trivy:ignore:AVD-AWS-0066
resource "aws_lambda_function" "pre_token" {
  function_name    = local.pre_token_name
  role             = aws_iam_role.pre_token.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "faro.identity.pre_token.handler"
  filename         = var.pre_token_zip
  source_code_hash = filebase64sha256(var.pre_token_zip)
  timeout          = 5

  environment {
    variables = {
      CUSTOMER_CLIENT_ID = aws_cognito_user_pool_client.this["customers"].id
      STAFF_CLIENT_ID    = aws_cognito_user_pool_client.this["staff"].id
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.pre_token.name
  }

  depends_on = [aws_iam_role_policy.pre_token]
}

resource "aws_lambda_permission" "cognito" {
  statement_id  = "AllowCognitoPreToken"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_token.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.this.arn
}
