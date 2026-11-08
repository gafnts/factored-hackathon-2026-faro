# The web app: a private bucket that CloudFront reads through origin access control, and the console API under /api on
# the same origin (ADR-0007).

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  csp = join("; ", [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self'",
    "font-src 'self'",
    "connect-src ${join(" ", concat(["'self'"], var.connect_src))}",
    "manifest-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
    "require-trusted-types-for 'script'",
  ])
}

# Access logs need a second bucket, and every object here is rebuilt from the repository.
#trivy:ignore:AVD-AWS-0089
resource "aws_s3_bucket" "site" {
  bucket           = "${var.prefix}-site-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}-an"
  bucket_namespace = "account-regional"
  force_destroy    = true
}

resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id
  versioning_configuration {
    status = "Enabled"
  }
}

# The site is built from public code.
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "site" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.site.arn, "${aws_s3_bucket.site.arn}/*"]
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

  # ListBucket makes a missing key a 404 rather than a 403.
  statement {
    sid       = "ReadThroughCloudFront"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.site.arn, "${aws_s3_bucket.site.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json

  depends_on = [aws_s3_bucket_public_access_block.site]
}

# Written at every apply, so an app client or a Runtime that changes reaches the site without a new upload.
resource "aws_s3_object" "config" {
  bucket        = aws_s3_bucket.site.id
  key           = "config.json"
  content       = jsonencode(var.config)
  content_type  = "application/json"
  cache_control = "no-cache"

  depends_on = [aws_s3_bucket_server_side_encryption_configuration.site]
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.prefix}-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "routes" {
  name    = "${var.prefix}-routes"
  runtime = "cloudfront-js-2.0"
  code    = file("${path.module}/routes.js")
  publish = true
}

resource "aws_cloudfront_response_headers_policy" "site" {
  name = "${var.prefix}-site"

  security_headers_config {
    content_security_policy {
      content_security_policy = local.csp
      override                = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
  }

  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
      override = true
    }
    items {
      header   = "Cross-Origin-Opener-Policy"
      value    = "same-origin"
      override = true
    }
  }
}

resource "aws_acm_certificate" "site" {
  count = var.domain_name == null ? 0 : 1

  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "site" {
  count = var.attach_domain ? 1 : 0

  certificate_arn = aws_acm_certificate.site[0].arn

  # ACM can take up to 30 minutes once the record is in DNS.
  timeouts {
    create = "45m"
  }
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

# Every viewer header but Host reaches the API, Authorization among them; API Gateway needs its own Host.
data "aws_cloudfront_origin_request_policy" "api" {
  name = "Managed-AllViewerExceptHostHeader"
}

# Its access logs go through CloudWatch Logs' delivery below, which the logging check doesn't read; a WAF is production
# work (OPS-11).
#trivy:ignore:AVD-AWS-0010
#trivy:ignore:AVD-AWS-0011
resource "aws_cloudfront_distribution" "site" {
  enabled         = true
  comment         = var.prefix
  http_version    = "http2and3"
  is_ipv6_enabled = true
  aliases         = var.attach_domain ? [var.domain_name] : []

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  # The console API on the site's own origin (ADR-0007's amendment of 2026-09-30): no CORS, and the CSP's 'self'.
  origin {
    origin_id   = "console"
    domain_name = var.api_origin

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # The guest entry's POST (ADR-0007, The guest entry). CloudFront allows GET and HEAD, or all seven methods, so it
  # gets a behavior of its own and /api/* keeps reads alone; a method the API has no route for gets its 404.
  ordered_cache_behavior {
    path_pattern               = "/api/guest"
    target_origin_id           = "console"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.api.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
  }

  # Never cached: each answer depends on the caller's token, and the API sends no-store.
  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = "console"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.api.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id
  }

  # Objects carry their own Cache-Control: hashed assets are immutable, index.html and config.json revalidate.
  default_cache_behavior {
    target_origin_id           = "site"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.routes.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = !var.attach_domain
    acm_certificate_arn            = var.attach_domain ? aws_acm_certificate_validation.site[0].certificate_arn : null
    ssl_support_method             = var.attach_domain ? "sni-only" : null
    minimum_protocol_version       = var.attach_domain ? "TLSv1.2_2025" : null
  }
}

# A line per request, so arrivals and their referrers can be counted (ADR-0004, data retention). Named under
# /aws/vendedlogs/ so CloudWatch Logs manages the delivery's resource policy.
#trivy:ignore:AVD-AWS-0017
resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/vendedlogs/cloudfront/${var.prefix}-site"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_delivery_source" "access" {
  name         = "${var.prefix}-site-access"
  log_type     = "ACCESS_LOGS"
  resource_arn = aws_cloudfront_distribution.site.arn
}

resource "aws_cloudwatch_log_delivery_destination" "access" {
  name          = "${var.prefix}-site-access"
  output_format = "json"

  delivery_destination_configuration {
    destination_resource_arn = aws_cloudwatch_log_group.access.arn
  }
}

# Never the path, which under /api names a case, the query string, cookies, or forwarded addresses.
resource "aws_cloudwatch_log_delivery" "access" {
  delivery_source_name     = aws_cloudwatch_log_delivery_source.access.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.access.arn
  record_fields = [
    "timestamp(ms)",
    "c-ip",
    "c-country",
    "cs-method",
    "cache-behavior-path-pattern",
    "sc-status",
    "sc-content-type",
    "cs(Referer)",
    "cs(User-Agent)",
    "x-edge-result-type",
    "time-taken",
  ]
}
