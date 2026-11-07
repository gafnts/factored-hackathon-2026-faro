# The integration and browser suites' role (ADR-0004, Operations, access controls, and its amendment of 2026-11-07):
# what their tests do, and nothing more. The boundary every role of the stack carries excludes IAM, so the tests that
# read role policies do it with the caller's credentials.

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  user_pool  = "arn:aws:cognito-idp:${local.region}:${local.account_id}:userpool/${var.user_pool_id}"
  table      = "arn:aws:dynamodb:${local.region}:${local.account_id}:table"
  log_group  = "arn:aws:logs:${local.region}:${local.account_id}:log-group"
}

# Trusted as the harness's role is: the other environment's deploy role is refused by its tag guard, and no service
# role of the stack may assume roles.
data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }
}

resource "aws_iam_role" "suites" {
  name                 = "${var.prefix}-suites"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "suites" {
  # The harness's rights: IAM scopes them to the pool, and the suites' code deletes only the users it created.
  statement {
    sid = "TestUsers"
    actions = [
      "cognito-idp:AdminAddUserToGroup",
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminDeleteUser",
      "cognito-idp:AdminGetUser",
      "cognito-idp:AdminInitiateAuth",
      "cognito-idp:AdminListGroupsForUser",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:AdminUserGlobalSignOut",
      "cognito-idp:ListUsersInGroup",
    ]
    resources = [local.user_pool]
  }

  # The suites' staff users sign in only once on the allowlist; their code adds and removes its own alone.
  statement {
    sid       = "StaffAllowlist"
    actions   = ["ssm:DeleteParameter", "ssm:PutParameter"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter${var.staff_allowlist}*"]
  }

  statement {
    sid       = "Overlay"
    actions   = ["dynamodb:DeleteItem", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"]
    resources = [var.overlay_table.arn]
  }

  statement {
    sid       = "ExecutionRecords"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = ["${local.table}/${var.runtime_tables.execution_records}"]
  }

  # The probe scans for a thread's checkpoints, to check none holds the typed number.
  statement {
    sid       = "Checkpoints"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = ["${local.table}/${var.runtime_tables.checkpoints}"]
  }

  # A test expires a confirmation, to play a lapsed one.
  statement {
    sid       = "Confirmations"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [var.confirmations_table.arn]
  }

  # The browser suite seeds a case for the console, and every test deletes the cases it made.
  statement {
    sid       = "Cases"
    actions   = ["dynamodb:DeleteItem", "dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [var.cases_table.arn]
  }

  # A test puts a user past the day's cap.
  statement {
    sid       = "UsageCounters"
    actions   = ["dynamodb:PutItem"]
    resources = ["${local.table}/${var.runtime_tables.usage_counters}"]
  }

  statement {
    sid       = "ToolsDataShape"
    actions   = ["dynamodb:DescribeTable", "dynamodb:ListImports", "dynamodb:ListTagsOfResource"]
    resources = [var.tools_data_table.arn]
  }

  statement {
    sid       = "ToolsDataImport"
    actions   = ["dynamodb:DescribeImport"]
    resources = ["${var.tools_data_table.arn}/import/*"]
  }

  # The tools data holds every customer's rows, is_fraud among them: the suites count them and read the stamp alone.
  statement {
    sid       = "ToolsDataCount"
    actions   = ["dynamodb:Scan"]
    resources = [var.tools_data_table.arn]
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["COUNT"]
    }
  }

  statement {
    sid       = "ToolsDataStamp"
    actions   = ["dynamodb:GetItem"]
    resources = [var.tools_data_table.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["META"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["pk", "sk", "stamp"]
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }

  statement {
    sid       = "FileHandoff"
    actions   = ["lambda:InvokeFunction"]
    resources = [var.file_handoff_function.arn]
  }

  # The tests look for a typed number in the Runtime's and the read tools' logs, and read the lines the alarms count.
  statement {
    sid     = "LogEvents"
    actions = ["logs:DescribeMetricFilters", "logs:FilterLogEvents"]
    resources = [
      "${local.log_group}:${var.runtime_log_group}:*",
      "${local.log_group}:/aws/lambda/${var.prefix}-reads:*",
    ]
  }

  # Every log group's retention, named by prefix.
  statement {
    sid       = "LogGroups"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["${local.log_group}:*"]
  }

  statement {
    sid       = "Spans"
    actions   = ["logs:StartQuery"]
    resources = ["${local.log_group}:aws/spans:*"]
  }

  # A query's results, its stop, and a filter pattern's test against sample lines take no resource.
  statement {
    sid       = "NoResource"
    actions   = ["logs:GetQueryResults", "logs:StopQuery", "logs:TestMetricFilter"]
    resources = ["*"]
  }

  # Listing alarms isn't authorized by the name prefix it filters on; it reads definitions, never data.
  statement {
    sid       = "Alarms"
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = ["*"]
  }

  statement {
    sid     = "ConsoleApi"
    actions = ["apigateway:GET"]
    resources = [
      "arn:aws:apigateway:${local.region}::/apis/${var.console_api_id}",
      "arn:aws:apigateway:${local.region}::/apis/${var.console_api_id}/*",
    ]
  }
}

resource "aws_iam_role_policy" "suites" {
  name   = "suites"
  role   = aws_iam_role.suites.id
  policy = data.aws_iam_policy_document.suites.json
}
