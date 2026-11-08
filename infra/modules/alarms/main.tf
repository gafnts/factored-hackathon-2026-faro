# The alarms (ADR-0004, Operations, and its amendments of 2026-10-01, 2026-11-03, 2026-11-07 and 2026-11-08; OPS-03), over metric
# filters on the Runtime's log group, which count the JSON lines the Runtime writes by source (EVL-13). A planned failure
# counts in none.

locals {
  namespace = var.prefix
  sources   = ["demo", "visitor", "evaluation", "unknown"]
  # Invited users' and visitors' turns, which the alarms on the agent's own failures read (ADR-0004's amendment of
  # 2026-11-07).
  live = ["demo", "visitor"]
  filters = {
    RuntimeErrors     = "{ ($.metric = \"turn_closed\" && $.outcome = \"error\") || $.metric = \"request_failed\" }"
    BlocksNotVerified = "{ $.metric = \"block_not_verified\" && $.planned IS FALSE }"
    ReplyChecks       = "{ $.metric = \"reply_check\" }"
    RepliesFellBack   = "{ $.metric = \"reply_check\" && $.fell_back IS TRUE }"
    ToolCallsFailed   = "{ $.metric = \"tool_call\" && $.outcome = \"failed\" && $.last IS TRUE && $.planned IS FALSE }"
    ModelCallsFailing = "{ $.metric = \"model_call\" && ($.outcome = \"spend_limit\" || $.outcome = \"key_rejected\" || $.outcome = \"model_not_found\" || $.outcome = \"invalid_request\") }"
    CapRefusals       = "{ $.metric = \"request_refused\" && $.code = \"global_limit\" }"
    SessionRefusals   = "{ $.metric = \"request_refused\" && $.code = \"session_refused\" }"
  }
}

# Notifications carry an alarm's name and state only, and CloudWatch can't publish to a topic under the AWS-managed key.
#trivy:ignore:AVD-AWS-0095
#trivy:ignore:AVD-AWS-0136
resource "aws_sns_topic" "alarms" {
  name = "${var.prefix}-alarms"
}

resource "aws_cloudwatch_log_metric_filter" "this" {
  for_each = local.filters

  name           = "${var.prefix}-${each.key}"
  log_group_name = var.log_group_name
  pattern        = each.value

  metric_transformation {
    name       = each.key
    namespace  = local.namespace
    value      = "1"
    unit       = "Count"
    dimensions = { source = "$.source" }
  }
}

resource "aws_cloudwatch_metric_alarm" "runtime_errors" {
  alarm_name          = "${var.prefix}-runtime-errors"
  alarm_description   = "A turn closed in error, or a request failed before its turn opened, from any source."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "errors"
    expression  = join(" + ", [for source in local.sources : "FILL(${source}, 0)"])
    label       = "Runtime errors"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = toset(local.sources)
    content {
      id = metric_query.value
      metric {
        metric_name = "RuntimeErrors"
        namespace   = local.namespace
        period      = 300
        stat        = "Sum"
        dimensions  = { source = metric_query.value }
      }
    }
  }

  depends_on = [aws_cloudwatch_log_metric_filter.this]
}

# Visitors and the evaluation share the provider's workspace, so a spent limit or a rejected key in either fails both.
resource "aws_cloudwatch_metric_alarm" "model_failing" {
  alarm_name          = "${var.prefix}-model-failing"
  alarm_description   = "A model call failed for a cause that fails every turn until a person acts (a spend limit, the key, the model's ID, or the request we build), from any source; the execution record names it."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "failing"
    expression  = join(" + ", [for source in local.sources : "FILL(${source}, 0)"])
    label       = "Model calls failing every turn"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = toset(local.sources)
    content {
      id = metric_query.value
      metric {
        metric_name = "ModelCallsFailing"
        namespace   = local.namespace
        period      = 300
        stat        = "Sum"
        dimensions  = { source = metric_query.value }
      }
    }
  }

  depends_on = [aws_cloudwatch_log_metric_filter.this]
}

resource "aws_cloudwatch_metric_alarm" "action_not_verified" {
  alarm_name          = "${var.prefix}-action-not-verified"
  alarm_description   = "A block that POL-37's reads didn't show, in invited users' or visitors' traffic."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "unverified"
    expression  = join(" + ", [for source in local.live : "FILL(${source}, 0)"])
    label       = "Blocks not verified"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = toset(local.live)
    content {
      id = metric_query.value
      metric {
        metric_name = "BlocksNotVerified"
        namespace   = local.namespace
        period      = 300
        stat        = "Sum"
        dimensions  = { source = metric_query.value }
      }
    }
  }

  depends_on = [aws_cloudwatch_log_metric_filter.this]
}

# A pause refuses turns too, and alarms on nothing, since an administrator set it.
resource "aws_cloudwatch_metric_alarm" "cap_reached" {
  alarm_name          = "${var.prefix}-cap-reached"
  alarm_description   = "The cap on the day's turns refused an invited user's or a visitor's turn; make cap prints the day's count."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "refused"
    expression  = join(" + ", [for source in local.live : "FILL(${source}, 0)"])
    label       = "Turns the cap refused"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = toset(local.live)
    content {
      id = metric_query.value
      metric {
        metric_name = "CapRefusals"
        namespace   = local.namespace
        period      = 3600
        stat        = "Sum"
        dimensions  = { source = metric_query.value }
      }
    }
  }

  depends_on = [aws_cloudwatch_log_metric_filter.this]
}

resource "aws_cloudwatch_metric_alarm" "tool_errors" {
  alarm_name          = "${var.prefix}-tool-errors"
  alarm_description   = "A tool call that failed after its last attempt, in invited users' or visitors' traffic."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "failed"
    expression  = join(" + ", [for source in local.live : "FILL(${source}, 0)"])
    label       = "Tool calls failed"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = toset(local.live)
    content {
      id = metric_query.value
      metric {
        metric_name = "ToolCallsFailed"
        namespace   = local.namespace
        period      = 300
        stat        = "Sum"
        dimensions  = { source = metric_query.value }
      }
    }
  }

  depends_on = [aws_cloudwatch_log_metric_filter.this]
}

# A share over an hour that holds too few checks to read says nothing, so it counts as zero.
resource "aws_cloudwatch_metric_alarm" "fallback_share" {
  alarm_name          = "${var.prefix}-reply-fallback-share"
  alarm_description   = "The share of the model's replies that fell back to fixed text, in invited users' and visitors' traffic (decision 8)."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.fallback_share
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "share"
    expression  = "IF(checked >= ${var.fewest_checks}, fell / checked, 0)"
    label       = "Fallback share"
    return_data = true
  }

  metric_query {
    id         = "checked"
    expression = join(" + ", [for source in local.live : "FILL(checked_${source}, 0)"])
  }

  metric_query {
    id         = "fell"
    expression = join(" + ", [for source in local.live : "FILL(fell_${source}, 0)"])
  }

  dynamic "metric_query" {
    for_each = toset(local.live)
    content {
      id = "checked_${metric_query.value}"
      metric {
        metric_name = "ReplyChecks"
        namespace   = local.namespace
        period      = 3600
        stat        = "Sum"
        dimensions  = { source = metric_query.value }
      }
    }
  }

  dynamic "metric_query" {
    for_each = toset(local.live)
    content {
      id = "fell_${metric_query.value}"
      metric {
        metric_name = "RepliesFellBack"
        namespace   = local.namespace
        period      = 3600
        stat        = "Sum"
        dimensions  = { source = metric_query.value }
      }
    }
  }

  depends_on = [aws_cloudwatch_log_metric_filter.this]
}

# Neither cause meets an honest chat, which keeps one runtime session per sign-in, so a refusal means a client opening
# sessions past its sign-in's limit, each holding a microVM until it is ended, or naming another user's.
resource "aws_cloudwatch_metric_alarm" "sessions_refused" {
  alarm_name          = "${var.prefix}-sessions-refused"
  alarm_description   = "A runtime session refused, past its sign-in's limit or bound to another user, from any source; the request_refused entries name the sign-in."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "refused"
    expression  = join(" + ", [for source in local.sources : "FILL(${source}, 0)"])
    label       = "Runtime sessions refused"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = toset(local.sources)
    content {
      id = metric_query.value
      metric {
        metric_name = "SessionRefusals"
        namespace   = local.namespace
        period      = 300
        stat        = "Sum"
        dimensions  = { source = metric_query.value }
      }
    }
  }

  depends_on = [aws_cloudwatch_log_metric_filter.this]
}
