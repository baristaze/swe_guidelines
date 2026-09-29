# Production: the `release` branch's environment. The graph is
# ../../modules/environment, the same one staging runs; this call is the
# parameter set that makes it production. Nothing here differs from staging
# but the numbers, the names, and the deletion protection a production
# database carries.

module "environment" {
  source    = "../../modules/environment"
  providers = { aws = aws, aws.us_east_1 = aws.us_east_1 }

  region      = var.region
  environment = var.environment

  # What the deploy workflow passes per run.
  api_image         = var.api_image
  maintenance_image = var.maintenance_image
  api_domain_name   = var.api_domain_name
  app_domain_name   = var.app_domain_name
  site_domain_name  = var.site_domain_name
  cors_origins      = var.cors_origins
  # The Acme App application of this environment's WorkOS environment. A
  # client id is public; the application's own API key is its secret.
  workos_client_id  = "client_PRODUCTION_PLACEHOLDER"
  portal_sentry_dsn = var.portal_sentry_dsn
  # This environment's Slack app (deployment/slack/manifest.production.json),
  # made when production opens. Empty until then: Slack is unconfigured here.
  slack_client_id = ""

  # The payment processor's account, the live account. The process refuses a key
  # whose mode is not this environment's.
  stripe_account_id = "acct_1UIfTS4Dj4HbbS1T"

  # Scale: size S, the demo posture (deployment/cloud/README.md prices
  # every size, and names what changes when real customers arrive: size L,
  # with a second zone for the database and the cache). Everything below is
  # what differs from staging: the graph does not. The pool is sized to the
  # instance at the autoscaling ceilings, so the flip below is safe: a
  # db.t4g.small takes about 180 connections, and (2 x 3 API + 1 worker) x
  # 2 pools x 10 + 8 for the one-off tasks is 148. The API task has half a
  # vCPU, and the 1 GB Fargate requires at that size: the collector sidecar
  # shares its CPU, and a page load's reads arrive together.
  vpc_cidr                     = "10.20.0.0/16"
  bucket_prefix                = "acme-production"
  database_instance_class      = "db.t4g.small"
  database_multi_az            = false
  database_deletion_protection = true
  database_pool_size           = 10
  cache_node_type              = "cache.t4g.micro"
  cache_node_count             = 1
  api_desired_count            = 1
  api_cpu                      = 512
  api_memory                   = 1024
  maintenance_desired_count    = 1
  maintenance_cpu              = 256
  maintenance_memory           = 512

  # Operations. `autoscaling_enabled` is the one flip: every lever below it
  # is on, so true scales the whole environment, and the flip is a pull
  # request a person reads. Off by default because an unattended scale-out
  # is a bill nobody approved. Each lever's floor is the desired count above.
  alarm_email             = var.alarm_email
  autoscaling_enabled     = false
  api_autoscaling         = { max = 3 }
  maintenance_autoscaling = { max = 1 }
  destroyable             = var.destroyable
}
