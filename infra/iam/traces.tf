# Transaction Search, set once per account and Region, since the Runtime's spans don't reach X-Ray without it
# (ADR-0004, Operations, Tracing). X-Ray writes every span to aws/spans, which both environments share; spans hold no
# text, and a KMS key would need a grant for X-Ray.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "spans" {
  name              = "aws/spans"
  retention_in_days = var.spans_retention_days
}

data "aws_iam_policy_document" "xray_spans" {
  statement {
    sid     = "SpansFromXRay"
    actions = ["logs:PutLogEvents", "logs:CreateLogStream"]
    resources = [
      "${aws_cloudwatch_log_group.spans.arn}:*",
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/application-signals/data:*",
    ]
    principals {
      type        = "Service"
      identifiers = ["xray.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:xray:${var.aws_region}:${local.account_id}:*"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "xray_spans" {
  policy_name     = "${var.project_name}-xray-spans"
  policy_document = data.aws_iam_policy_document.xray_spans.json
}

resource "aws_xray_trace_segment_destination" "this" {
  destination = "CloudWatchLogs"

  depends_on = [aws_cloudwatch_log_resource_policy.xray_spans]
}

resource "aws_xray_indexing_rule" "default" {
  name = "Default"

  rule {
    probabilistic {
      desired_sampling_percentage = var.spans_indexed_percentage
    }
  }

  depends_on = [aws_xray_trace_segment_destination.this]
}
