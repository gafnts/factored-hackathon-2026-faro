# Dataset snapshots (docs/adr/0002). Outside the environment stacks, so destroying one never deletes the data.
data "aws_caller_identity" "current" {}

# Access logs need a second bucket; an audit trail of reads is remaining deployment work.
#trivy:ignore:AVD-AWS-0089
resource "aws_s3_bucket" "data" {
  bucket           = "${var.project_name}-data-${data.aws_caller_identity.current.account_id}-${var.aws_region}-an"
  bucket_namespace = "account-regional"

  # The source can no longer be downloaded, so a snapshot can't be rebuilt: a destroy stops while anything is here.
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

# The data is synthetic, and a KMS key would need a grant in every reader's role.
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "data_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
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

  # Snapshots are immutable: an object can be created, never overwritten.
  statement {
    sid       = "DenyUnconditionalWrites"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Null"
      variable = "s3:if-none-match"
      values   = ["true"]
    }
    condition {
      test     = "Bool"
      variable = "s3:ObjectCreationOperation"
      values   = ["true"]
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = data.aws_iam_policy_document.data_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.data]
}

# DynamoDB logs every import in the account here, and creates the group without a retention if it doesn't exist. No
# Environment tag, so both environments' imports write to it; entries name objects and lines, never items.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "imports" {
  name              = "/aws-dynamodb/imports"
  retention_in_days = var.log_retention_days
}
