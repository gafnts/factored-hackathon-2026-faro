# The AgentCore Runtime, its code, the model key, and the checkpoints (ADR-0004).

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  region       = data.aws_region.current.region
  agentcore    = "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}"
  runtime_name = "${replace(var.prefix, "-", "_")}_agent"
  # The Runtime's ID is its name and a suffix, so its workload identity and log group match this before it exists.
  runtime_ids = "${local.runtime_name}-*"
  # AgentCore names its own span for the endpoint, and CloudWatch's GenAI page counts an agent's spans by that name.
  service_name = "${local.runtime_name}.DEFAULT"
  workload_identities = [
    "${local.agentcore}:workload-identity-directory/default",
    "${local.agentcore}:workload-identity-directory/default/workload-identity/${local.runtime_ids}",
  ]
  # Named before the Runtime exists, so its environment and its role can name the parameter that holds its ARN.
  runtime_arn_parameter = "/${var.prefix}/runtime/arn"
}

# Access logs need a second bucket, and every object here is rebuilt from the repository.
#trivy:ignore:AVD-AWS-0089
resource "aws_s3_bucket" "artifacts" {
  bucket           = "${var.prefix}-artifacts-${local.account_id}-${local.region}-an"
  bucket_namespace = "account-regional"
  force_destroy    = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

# The zips are built from public code.
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "artifacts" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.artifacts.json

  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

resource "aws_s3_object" "runtime" {
  bucket      = aws_s3_bucket.artifacts.id
  key         = "runtime/${filesha256(var.runtime_zip)}.zip"
  source      = var.runtime_zip
  source_hash = filemd5(var.runtime_zip)

  depends_on = [aws_s3_bucket_server_side_encryption_configuration.artifacts]
}

# Created by infra/iam and filled by make model-key before the first apply, so the key never reaches state, a plan, or
# CI (CONTRIBUTING.md, step 3.6).
data "aws_secretsmanager_secret" "model_key" {
  name = "${var.prefix}-anthropic-api-key"
}

resource "aws_bedrockagentcore_api_key_credential_provider" "model" {
  name                  = "${var.prefix}-anthropic"
  api_key_secret_source = "EXTERNAL"

  api_key_secret_config {
    secret_id = data.aws_secretsmanager_secret.model_key.arn
    json_key  = "api_key"
  }
}

# Checkpoints expire after 7 days (ADR-0004, Stores) and are never restored.
#trivy:ignore:AVD-AWS-0024
#trivy:ignore:AVD-AWS-0025
resource "aws_dynamodb_table" "checkpoints" {
  name         = "${var.prefix}-checkpoints"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# Bindings live as long as the runtime session they name can (ADR-0004, Stores).
#trivy:ignore:AVD-AWS-0024
#trivy:ignore:AVD-AWS-0025
resource "aws_dynamodb_table" "session_bindings" {
  name         = "${var.prefix}-session-bindings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "runtime_session_id"

  attribute {
    name = "runtime_session_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# Kept 90 days (OPS-10); point-in-time recovery and a key of our own are production work (OPS-11).
#trivy:ignore:AVD-AWS-0024
#trivy:ignore:AVD-AWS-0025
resource "aws_dynamodb_table" "execution_records" {
  name         = "${var.prefix}-execution-records"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sign_in"
  range_key    = "entry_key"

  attribute {
    name = "sign_in"
    type = "S"
  }

  attribute {
    name = "entry_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# Each window's count expires when the window ends (decision 21).
#trivy:ignore:AVD-AWS-0024
#trivy:ignore:AVD-AWS-0025
resource "aws_dynamodb_table" "usage_counters" {
  name         = "${var.prefix}-usage-counters"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "counter"
  range_key    = "window"

  attribute {
    name = "counter"
    type = "S"
  }

  attribute {
    name = "window"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# Written once, so a fork gets one; make cap sets it afterwards, and a deploy never undoes a pause (decision 21). A cap
# written before the guests' gets them from make cap GUESTS=, since a change here never reaches it.
resource "aws_dynamodb_table_item" "cap" {
  table_name = aws_dynamodb_table.usage_counters.name
  hash_key   = aws_dynamodb_table.usage_counters.hash_key
  range_key  = aws_dynamodb_table.usage_counters.range_key
  item = jsonencode({
    counter = { S = "cap" }
    window  = { S = "current" }
    turns   = { N = tostring(var.turns_cap) }
    guests  = { N = tostring(var.guests_cap) }
  })

  lifecycle {
    ignore_changes = [item]
  }
}

data "aws_iam_policy_document" "runtime_trust" {
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
      values   = ["${local.agentcore}:*"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name                 = "${var.prefix}-runtime"
  assume_role_policy   = data.aws_iam_policy_document.runtime_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

# No logs:CreateLogGroup: a group the Runtime creates has no retention and survives a destroy (spike S4).
data "aws_iam_policy_document" "runtime" {
  statement {
    actions   = ["logs:DescribeLogStreams", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/${local.runtime_ids}"]
  }
  statement {
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:*"]
  }
  statement {
    actions = [
      "xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets",
      "xray:PutSpans", "xray:PutSpansForIndexing",
    ]
    resources = ["*"]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }
  statement {
    actions   = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT"]
    resources = local.workload_identities
  }
  # A customer's JWT is always present, so a token for a bare user ID is never needed (ADR-0004, Operations).
  statement {
    effect    = "Deny"
    actions   = ["bedrock-agentcore:GetWorkloadAccessTokenForUserId", "bedrock-agentcore:InvokeAgentRuntimeForUser"]
    resources = ["*"]
  }
  statement {
    actions = ["bedrock-agentcore:GetResourceApiKey"]
    resources = concat(local.workload_identities, [
      "${local.agentcore}:token-vault/default",
      aws_bedrockagentcore_api_key_credential_provider.model.credential_provider_arn,
    ])
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [data.aws_secretsmanager_secret.model_key.arn]
  }
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [aws_dynamodb_table.checkpoints.arn]
  }
  # Put only: the Runtime appends to the record and never rewrites or deletes an entry (ADR-0004's amendment).
  statement {
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.session_bindings.arn, aws_dynamodb_table.execution_records.arn]
  }
  # Creates a confirmation and changes its status, always on a condition; it never reads the sandbox or the tools' data.
  statement {
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [var.confirmations_table.arn]
  }
  # Counts a turn, or a sign-in's new runtime session, on a condition, and reads no count back; the cap is an
  # administrator's to set (decision 21).
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.usage_counters.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["counter", "window", "turns", "sessions", "expires_at"]
    }
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["sign_in#*", "user#*", "day"]
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:ReturnValues"
      values   = ["NONE"]
    }
  }
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.usage_counters.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["cap"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = ["counter", "window", "turns"]
    }
    condition {
      test     = "StringEquals"
      variable = "dynamodb:Select"
      values   = ["SPECIFIC_ATTRIBUTES"]
    }
  }
  # Handoffs are filed through file_handoff, so the Runtime writes no case itself.
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [var.file_handoff_function.arn]
  }
  # Its own ARN, which StopRuntimeSession's URL names, to end a runtime session refused at its sign-in's limit.
  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter${local.runtime_arn_parameter}"]
  }
}

resource "aws_iam_role_policy" "runtime" {
  name   = "runtime"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_bedrockagentcore_agent_runtime" "this" {
  agent_runtime_name = local.runtime_name
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_13"
      code {
        s3 {
          bucket = aws_s3_bucket.artifacts.id
          prefix = aws_s3_object.runtime.key
        }
      }
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "AGUI"
  }

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = var.discovery_url
      allowed_clients = var.allowed_clients

      custom_claim {
        inbound_token_claim_name       = "cognito:groups"
        inbound_token_claim_value_type = "STRING_ARRAY"
        authorizing_claim_match_value {
          claim_match_operator = "CONTAINS"
          claim_match_value {
            match_value_string = var.required_group
          }
        }
      }
    }
  }

  request_header_configuration {
    request_header_allowlist = ["Authorization"]
  }

  lifecycle_configuration = [{
    idle_runtime_session_timeout = 900
    max_lifetime                 = 28800
  }]

  environment_variables = {
    CUSTOMER_CLIENT_ID      = var.customer_client_id
    GATEWAY_URL             = var.gateway_url
    GATEWAY_TARGETS         = jsonencode(var.gateway_targets)
    TOOLS_DATA              = jsonencode(var.tools_data)
    CHECKPOINTS_TABLE       = aws_dynamodb_table.checkpoints.name
    SESSION_BINDINGS_TABLE  = aws_dynamodb_table.session_bindings.name
    EXECUTION_RECORDS_TABLE = aws_dynamodb_table.execution_records.name
    CONFIRMATIONS_TABLE     = var.confirmations_table.name
    FILE_HANDOFF_FUNCTION   = var.file_handoff_function.name
    MODEL_KEY_PROVIDER      = aws_bedrockagentcore_api_key_credential_provider.model.name
    USAGE_COUNTERS_TABLE    = aws_dynamodb_table.usage_counters.name
    RUNTIME_ARN_PARAMETER   = local.runtime_arn_parameter
    TURNS_PER_MINUTE        = tostring(var.turns_per_minute)
    TURNS_PER_DAY           = tostring(var.turns_per_day)
    # The SDK then fails without a workload token instead of making a local workload identity (spike S4).
    DOCKER_CONTAINER = "1"
    # ADOT's exporter marks its requests as an agent's, as AgentCore's setup does, without moving any attribute into a
    # log event (ADR-0004, Operations, Tracing).
    AGENT_OBSERVABILITY_ENABLED          = "true"
    AWS_GENAI_CONTENT_EXTRACTION_OPT_OUT = "true"
    OTEL_SERVICE_NAME                    = local.service_name
  }

  depends_on = [aws_iam_role_policy.runtime]
}

# The Runtime's environment can't hold its own ARN, which Terraform knows only once the Runtime exists (ADR-0004,
# Ending a runtime session). An ARN, not a secret.
resource "aws_ssm_parameter" "runtime_arn" {
  name  = local.runtime_arn_parameter
  type  = "String"
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

# Logs hold masked text only, and a KMS key would need a grant for every writer.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/aws/bedrock-agentcore/runtimes/${aws_bedrockagentcore_agent_runtime.this.agent_runtime_id}-DEFAULT"
  retention_in_days = var.log_retention_days
}

# AgentCore's own span for each request, which roots the trace. Never APPLICATION_LOGS, which record the request as the
# customer sent it, before the entrypoint masks it (ADR-0004, Card numbers).
resource "aws_cloudwatch_log_delivery_source" "traces" {
  name         = "${var.prefix}-runtime-traces"
  log_type     = "TRACES"
  resource_arn = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

resource "aws_cloudwatch_log_delivery_destination" "xray" {
  name                      = "${var.prefix}-runtime-xray"
  delivery_destination_type = "XRAY"
}

resource "aws_cloudwatch_log_delivery" "traces" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.traces.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.xray.arn
}
