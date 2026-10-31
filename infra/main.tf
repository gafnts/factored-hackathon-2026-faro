provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  prefix = "${var.project_name}-${var.environment}"
  # Required on every IAM role this stack creates (see infra/iam).
  permissions_boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${local.prefix}-boundary"
  # Written by make build, which make plan runs first.
  build = "${path.root}/../build"
  # Named as infra/dataset names it; the exports live beside the snapshots.
  data_bucket = "${var.project_name}-data-${data.aws_caller_identity.current.account_id}-${var.aws_region}-an"
}

module "identity" {
  source = "./modules/identity"

  prefix                   = local.prefix
  permissions_boundary_arn = local.permissions_boundary_arn
  log_retention_days       = var.log_retention_days
  pre_token_zip            = "${local.build}/pre_token.zip"
}

module "tools_data" {
  source = "./modules/tools_data"

  prefix      = local.prefix
  export      = var.tools_data_export
  data_bucket = local.data_bucket
  contract    = "${path.root}/../src/faro/contracts/tools-data.schema.json"
}

module "sandbox" {
  source = "./modules/sandbox"

  prefix   = local.prefix
  contract = "${path.root}/../src/faro/contracts/overlay.schema.json"
}

module "gateway" {
  source = "./modules/gateway"

  prefix                   = local.prefix
  permissions_boundary_arn = local.permissions_boundary_arn
  log_retention_days       = var.log_retention_days
  discovery_url            = module.identity.discovery_url
  allowed_clients          = [module.identity.customer_client_id]
  tools_zip                = "${local.build}/tools.zip"
  tools_data_table         = module.tools_data.table_name
  tools_data_table_arn     = module.tools_data.table_arn
  tools_data_attributes    = module.tools_data.readable_attributes
  overlay_table            = module.sandbox.overlay
  overlay_attributes       = module.sandbox.readable_attributes
  fault_attributes         = module.sandbox.counted_attributes
  confirmations_table      = module.sandbox.confirmations
}

module "handoff" {
  source = "./modules/handoff"

  prefix                   = local.prefix
  permissions_boundary_arn = local.permissions_boundary_arn
  log_retention_days       = var.log_retention_days
  tools_zip                = "${local.build}/tools.zip"
  tools_data_table         = { name = module.tools_data.table_name, arn = module.tools_data.table_arn }
  tools_data_attributes    = module.tools_data.attributes
  customer_client_id       = module.identity.customer_client_id
  execution_records_table  = module.runtime.execution_records_table
  overlay_table            = module.sandbox.overlay
}

module "runtime" {
  source = "./modules/runtime"

  prefix                   = local.prefix
  permissions_boundary_arn = local.permissions_boundary_arn
  log_retention_days       = var.log_retention_days
  discovery_url            = module.identity.discovery_url
  allowed_clients          = [module.identity.customer_client_id]
  customer_client_id       = module.identity.customer_client_id
  required_group           = "customer"
  gateway_url              = module.gateway.gateway_url
  gateway_targets          = module.gateway.targets
  confirmations_table      = module.sandbox.confirmations
  file_handoff_function    = module.handoff.function
  tools_data               = { stamp = module.tools_data.stamp, clock = module.tools_data.clock }
  runtime_zip              = "${local.build}/runtime.zip"
  turns_per_minute         = var.turns_per_minute
  turns_per_day            = var.turns_per_day
}

module "alarms" {
  source = "./modules/alarms"

  prefix         = local.prefix
  log_group_name = module.runtime.runtime_log_group
}

module "console" {
  source = "./modules/console"

  prefix                   = local.prefix
  permissions_boundary_arn = local.permissions_boundary_arn
  log_retention_days       = var.log_retention_days
  tools_zip                = "${local.build}/tools.zip"
  issuer                   = module.identity.issuer
  staff_client_id          = module.identity.staff_client_id
  cases_table              = module.handoff.cases_table
  execution_records_table  = module.runtime.execution_records_table
}

module "evaluation" {
  source = "./modules/evaluation"

  prefix                   = local.prefix
  permissions_boundary_arn = local.permissions_boundary_arn
  user_pool_id             = module.identity.user_pool_id
  overlay_table            = module.sandbox.overlay
  confirmations_table      = module.sandbox.confirmations
  execution_records_table  = module.runtime.execution_records_table
  cases_table              = module.handoff.cases_table
  file_handoff_function    = module.handoff.function
}

module "site" {
  source = "./modules/site"

  prefix = local.prefix
  config = {
    region             = var.aws_region
    user_pool_id       = module.identity.user_pool_id
    customer_client_id = module.identity.customer_client_id
    staff_client_id    = module.identity.staff_client_id
    runtime_url        = module.runtime.invoke_url
  }
  connect_src = [
    "https://cognito-idp.${var.aws_region}.amazonaws.com",
    "https://bedrock-agentcore.${var.aws_region}.amazonaws.com",
  ]
  domain_name   = var.domain_name
  attach_domain = var.attach_domain
  api_origin    = module.console.origin_domain
}
