# Saved Logs Insights queries over the Runtime's spans (ADR-0004, Operations, Tracing; OPS-01): a turn's latency, its
# time by node, and its tokens and cost. The spans live in the account's aws/spans beside the other environment's, so
# each query reads its own Runtime's alone.

locals {
  spans = "filter `resource.attributes.service.name` = \"${var.service_name}\""
  queries = {
    "turn-latency"   = <<-QUERY
      ${local.spans}
      | filter name = "invoke_agent card_support"
      | fields durationNano / 1000000 as ms
      | stats count(*) as requests, avg(ms) as avg_ms, pct(ms, 50) as p50_ms, pct(ms, 95) as p95_ms by `attributes.faro.input` as input
    QUERY
    "time-by-node"   = <<-QUERY
      ${local.spans}
      | filter ispresent(`attributes.faro.node`)
      | fields durationNano / 1000000 as ms
      | stats count(*) as runs, avg(ms) as avg_ms, pct(ms, 95) as p95_ms, sum(ms) as total_ms by `attributes.faro.node` as node
      | sort total_ms desc
    QUERY
    "tokens-by-turn" = <<-QUERY
      ${local.spans}
      | filter `attributes.gen_ai.operation.name` = "chat"
      | stats count(*) as calls, sum(`attributes.gen_ai.usage.input_tokens`) as input_tokens, sum(`attributes.gen_ai.usage.output_tokens`) as output_tokens, sum(`attributes.gen_ai.usage.cache_read.input_tokens`) as cache_read_tokens, sum(`attributes.faro.cost_usd`) as cost_usd by traceId
      | sort cost_usd desc
    QUERY
  }
}

resource "aws_cloudwatch_query_definition" "spans" {
  for_each = local.queries

  name            = "${var.prefix}/${each.key}"
  log_group_names = ["aws/spans"]
  query_string    = each.value
}
