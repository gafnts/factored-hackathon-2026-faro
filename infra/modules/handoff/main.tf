# The handoff cases and file_handoff, a Lambda off the Gateway that only the Runtime's role invokes (ADR-0004, Where the
# tools run; ADR-0007, Handoffs as cases, and their amendments of 2026-09-30).

locals {
  function_name = "${var.prefix}-file-handoff"
}

# Kept 90 days (OPS-10); point-in-time recovery and a key of our own are production work (OPS-11).
#trivy:ignore:AVD-AWS-0024
#trivy:ignore:AVD-AWS-0025
resource "aws_dynamodb_table" "cases" {
  name         = "${var.prefix}-handoff-cases"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "queue_key"
    type = "S"
  }

  attribute {
    name = "queue_order"
    type = "S"
  }

  # Only filed cases carry queue_key, so drafts and reference items stay out of the queue. Read in descending order,
  # queue_order lists urgent cases first and each priority's newest first.
  global_secondary_index {
    name               = "by_queue"
    projection_type    = "INCLUDE"
    non_key_attributes = ["reference", "priority", "reason_code", "language", "filed_at", "flagged"]

    key_schema {
      attribute_name = "queue_key"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "queue_order"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# Logs hold masked text only, and a KMS key would need a grant for every writer.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "file_handoff" {
  name              = "/aws/lambda/${local.function_name}"
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

resource "aws_iam_role" "file_handoff" {
  name                 = local.function_name
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

# Cognito's GetUser is authorized by the customer's token, not by IAM. is_fraud is read from a transaction's own item,
# never through an index (POL-40).
data "aws_iam_policy_document" "file_handoff" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.file_handoff.arn}:*"]
  }
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [var.tools_data_table.arn]
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
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.cases.arn]
  }
  # Reads the turns a case cites, to check its evidence; only the Runtime writes them.
  statement {
    actions   = ["dynamodb:Query"]
    resources = [var.execution_records_table.arn]
  }
  # A fixture transaction's is_fraud, by its key (POL-40).
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [var.overlay_table.arn]
  }
}

resource "aws_iam_role_policy" "file_handoff" {
  name   = "file-handoff"
  role   = aws_iam_role.file_handoff.id
  policy = data.aws_iam_policy_document.file_handoff.json
}

# The execution record is the prototype's trace (ADR-0004, Operations).
#trivy:ignore:AVD-AWS-0066
resource "aws_lambda_function" "file_handoff" {
  function_name    = local.function_name
  role             = aws_iam_role.file_handoff.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "faro.tools.handoff.handler"
  filename         = var.tools_zip
  source_code_hash = filebase64sha256(var.tools_zip)
  memory_size      = 256
  timeout          = 10

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.file_handoff.name
  }

  environment {
    variables = {
      TOOLS_DATA_TABLE        = var.tools_data_table.name
      CASES_TABLE             = aws_dynamodb_table.cases.name
      EXECUTION_RECORDS_TABLE = var.execution_records_table.name
      CUSTOMER_CLIENT_ID      = var.customer_client_id
      OVERLAY_TABLE           = var.overlay_table.name
    }
  }

  depends_on = [aws_iam_role_policy.file_handoff]
}
