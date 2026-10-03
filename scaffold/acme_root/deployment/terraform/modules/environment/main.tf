# One environment, whole: the graph every environment instantiates, from the
# network to the three processes. An environment root is this module called once
# with its parameter set and its backend, so there is one place a resource is
# added and no copy to keep in step. Each application process is one instance
# of the service module. Production promotes the images staging already ran,
# by digest, behind an approval gate: the image variables are the digests the
# deploy workflows pass.
#
# The module takes the `aws.us_east_1` alias as well as the default provider:
# CloudFront reads certificates from that region only.

locals {
  # The company site is made only when it has a name (see the site below).
  site_enabled = var.site_domain_name != ""

  # What the migrate task does to a database is decided by these files alone:
  # the migrations, and the runner and the logins code the migrate command
  # imports. deployment/migration-inputs.json names them, and
  # om/tests/unit/test_migration_inputs.py holds the list to what the
  # command imports. The fingerprint is of the commit being applied, the one
  # its API image was built from, so a change to it is a release that brings
  # the database something new. The root sits four levels up.
  repository_root = "${path.module}/../../../.."
  migration_files = sort(distinct(flatten([
    for pattern in jsondecode(file("${local.repository_root}/deployment/migration-inputs.json")).paths :
    fileset(local.repository_root, pattern)
  ])))
  migration_fingerprint = sha256(join("\n", [
    for f in local.migration_files : "${f} ${filesha256("${local.repository_root}/${f}")}"
  ]))

  # The migrate task's secrets: the serving logins' URLs, which
  # `ensure-logins` sets the passwords from, and the master's and the
  # migration login's.
  migrate_secrets = merge(local.process_secrets, {
    ACME_DATABASE_MIGRATION_URL = module.secrets.database_migration_url_secret_arn
    ACME_DATABASE_MASTER_URL    = module.secrets.database_master_url_secret_arn
  })

  # The environment every process reads, mirrored from .env.example. Every
  # backend is the hosted one, and ACME_ENVIRONMENT makes the process refuse
  # anything else at boot.
  process_environment = {
    ACME_ENVIRONMENT         = var.environment
    ACME_CACHE_BACKEND       = "valkey"
    ACME_TOPICS_BACKEND      = "valkey"
    ACME_VALKEY_URL          = module.cache.url
    ACME_BUCKETS_BACKEND     = "s3"
    ACME_S3_BUCKET_PREFIX    = module.buckets.prefix
    ACME_QUEUES_BACKEND      = "sqs"
    ACME_SQS_QUEUE_PREFIX    = module.queue.prefix
    ACME_SECRETS_BACKEND     = "aws"
    ACME_SECRETS_NAME_PREFIX = module.secrets.application_prefix
    ACME_AWS_REGION          = var.region
    ACME_LOG_JSON            = "true"
    ACME_DATABASE_POOL_SIZE  = tostring(var.database_pool_size)
    # Read by no code. A rotation (database_password_version raised) changes
    # the task definition through this line, so every service rolls and its
    # new tasks start with the new URL; without it the old tasks would keep
    # the old password until their next reconnect failed.
    ACME_DATABASE_PASSWORD_VERSION = tostring(var.database_password_version)
  }

  # How people sign in and out, read at boot by every process that runs the
  # API's image: the service, and the migrate and grant tasks, which boot the
  # same settings and refuse a sign-in or a sign-out that comes back anywhere
  # but this environment's own portal. People sign in through the Acme App,
  # the WorkOS application of this environment; its sign-out URIs list the
  # same `/signed-out` page.
  api_sign_in_environment = {
    ACME_IDENTITY_PROVIDER     = "workos"
    ACME_WORKOS_CLIENT_ID      = var.workos_client_id
    ACME_SIGN_IN_REDIRECT_URIS = jsonencode(["https://${var.app_domain_name}/auth/callback"])
    ACME_SIGN_OUT_RETURN_URIS  = jsonencode(["https://${var.app_domain_name}/signed-out"])
  }

  # A serving process connects as the runtime login, and as the system login
  # for the system scope, each with a pool of its own. The master's and the
  # migration login's URLs reach the migrate task alone.
  process_secrets = {
    ACME_DATABASE_URL        = module.secrets.database_url_secret_arn
    ACME_DATABASE_SYSTEM_URL = module.secrets.database_system_url_secret_arn
    ACME_SENTRY_DSN          = module.secrets.sentry_dsn_secret_arn
  }

  # A one-off task opens small pools: it runs one command, not requests.
  # deployment/cloud/README.md counts them in the connection budget.
  one_off_environment = merge(local.process_environment, {
    ACME_DATABASE_POOL_SIZE = "2"
  })

  # The API's admission bounds follow its pool, generously: a burst waits on
  # a checkout, which has a bound of its own, and only a flood is refused.
  # Thirty-two reads in flight per connection and half as many writes, which
  # no demo, traffic run, or person clicking fast comes near.
  admission_limit_reads  = 32 * var.database_pool_size
  admission_limit_writes = 16 * var.database_pool_size

  process_policies = [
    module.queue.policy_arn,
    module.buckets.policy_arn,
    module.secrets.policy_arn,
  ]
}

module "network" {
  source = "../network"

  environment = var.environment
  cidr        = var.vpc_cidr
}

module "cluster" {
  source = "../cluster"

  environment = var.environment
}

# The master password exists for the run and nowhere else: the database and
# the secret both take it write-only. Raising database_password_version
# writes a new one to both.
ephemeral "random_password" "database" {
  length  = 32
  special = false
}

module "database" {
  source = "../database"

  master_password         = ephemeral.random_password.database.result
  master_password_version = var.database_password_version

  environment         = var.environment
  subnet_ids          = module.network.data_subnet_ids
  security_group_ids  = [module.network.database_security_group_id]
  instance_class      = var.database_instance_class
  multi_az            = var.database_multi_az
  deletion_protection = var.database_deletion_protection
  destroyable         = var.destroyable
}

module "cache" {
  source = "../cache"

  environment        = var.environment
  subnet_ids         = module.network.data_subnet_ids
  security_group_ids = [module.network.cache_security_group_id]
  node_type          = var.cache_node_type
  node_count         = var.cache_node_count
}

module "queue" {
  source = "../queue"

  environment = var.environment
  prefix      = "acme-${var.environment}-"
  queues      = ["webhooks"] # acme.infra.queues.Queues
}

module "buckets" {
  source = "../buckets"

  environment = var.environment
  prefix      = var.bucket_prefix
  buckets     = ["user-file-uploads", "exports"] # acme.infra.buckets.Buckets
  destroyable = var.destroyable
  # The portal posts a file straight to the uploads bucket with a form the
  # API signed, and fetches it back by a signed link.
  browser_buckets = ["user-file-uploads"]
  browser_origins = concat(["https://${var.app_domain_name}"], var.cors_origins)
  # Every key the media namespace writes is <org_id>/media/<purpose>/<id>.
  object_key_patterns = { "user-file-uploads" = "*/media/*" }
}

module "secrets" {
  source = "../secrets"

  environment               = var.environment
  prefix                    = "acme/${var.environment}/"
  database_password         = ephemeral.random_password.database.result
  database_password_version = var.database_password_version
  database_username         = module.database.username
  database_address          = module.database.address
  database_port             = module.database.port
  database_name             = module.database.db_name
  destroyable               = var.destroyable
}

# Two public names, each at the apex of a Route 53 zone of its own that the
# account's bootstrap root made and delegated from Cloudflare: the API at the
# load balancer, the portal at CloudFront. Each gets a DNS-validated
# certificate; the portal's is in us-east-1, the only region CloudFront reads
# certificates from.
#
# The company site's name is the third, and it is not delegated: it is a
# record in the Cloudflare zone (the apex in production, staging.acme.example in
# staging), which only the create run writes. Its certificate is the
# bootstrap root's, validated by a record that run wrote; this finds it by
# its name, once issued.
#
# The site is optional. With no name (site_domain_name empty), there is no
# certificate lookup and no site: everything else plans and applies as it
# would, and the deploy workflows pass an empty name until the create run
# has set SITE_DOMAIN_NAME and the certificate is issued.
data "aws_route53_zone" "api" {
  name = var.api_domain_name
}

data "aws_route53_zone" "app" {
  name = var.app_domain_name
}

data "aws_acm_certificate" "site" {
  count    = local.site_enabled ? 1 : 0
  provider = aws.us_east_1

  domain      = var.site_domain_name
  statuses    = ["ISSUED"]
  most_recent = true
}

module "api_certificate" {
  source = "../certificate"

  environment = var.environment
  domain_name = var.api_domain_name
  zone_id     = data.aws_route53_zone.api.zone_id
}

module "app_certificate" {
  source    = "../certificate"
  providers = { aws = aws.us_east_1 }

  environment = var.environment
  domain_name = var.app_domain_name
  zone_id     = data.aws_route53_zone.app.zone_id
}

module "load_balancer" {
  source = "../load_balancer"

  environment        = var.environment
  vpc_id             = module.network.vpc_id
  subnet_ids         = module.network.public_subnet_ids
  security_group_ids = [module.network.load_balancer_security_group_id]
  certificate_arn    = module.api_certificate.arn
}

# The portal and the company site are the same kind of thing, static files
# behind CloudFront, so they are one module called twice. The portal reads its
# runtime config, routes client paths, and serves the API's paths from its own
# distribution, by the load balancer at the API's domain name; the site calls
# nothing and answers a missing path with its own not-found page.
#
# The page calls the API same-origin (apiUrl empty means the page's origin),
# so no request it makes is cross-origin and no browser sends a preflight.
# The API's own domain name serves the rest: the providers' deliveries, the
# command line, and the operators.
module "portal" {
  source = "../static_site"

  name            = "portal"
  environment     = var.environment
  bucket_name     = "${var.bucket_prefix}-portal"
  domain_name     = var.app_domain_name
  certificate_arn = module.app_certificate.arn
  api_domain_name = var.api_domain_name
  api_edge_secret = module.secrets.edge_secret
  store_origins   = module.buckets.origins["user-file-uploads"]
  sentry_dsn      = var.portal_sentry_dsn
  client_routes   = true
  runtime_config = {
    apiUrl      = ""
    sentryDsn   = var.portal_sentry_dsn
    environment = var.environment
  }
  destroyable = var.destroyable
}

module "site" {
  source = "../static_site"
  count  = local.site_enabled ? 1 : 0

  name            = "site"
  environment     = var.environment
  bucket_name     = "${var.bucket_prefix}-site"
  domain_name     = var.site_domain_name
  certificate_arn = data.aws_acm_certificate.site[0].arn
  not_found_page  = "/404.html"
  destroyable     = var.destroyable
}

module "domain_records" {
  source = "../domain_records"

  api_zone_id              = data.aws_route53_zone.api.zone_id
  app_zone_id              = data.aws_route53_zone.app.zone_id
  api_domain_name          = var.api_domain_name
  app_domain_name          = var.app_domain_name
  load_balancer_dns_name   = module.load_balancer.dns_name
  load_balancer_zone_id    = module.load_balancer.zone_id
  distribution_domain_name = module.portal.distribution_domain_name
  distribution_zone_id     = module.portal.distribution_zone_id
}

# The two one-off tasks, both on the API image. The migrate task is the one
# place the master's and the migration login's URLs are injected: it runs
# `ensure-logins` as the master (the three logins, their passwords from their
# URLs, the ownership moved to the migration login, the grants), then the
# migrations as the migration login. It holds the runtime and system URLs as
# well, since `ensure-logins` sets those logins' passwords from them.
module "migrate" {
  source = "../task"

  name        = "migrate"
  image       = var.api_image
  environment = var.environment
  command     = ["acme-api", "migrate", "--all"]

  environment_variables = merge(local.one_off_environment, local.api_sign_in_environment, {
    ACME_SERVICE_NAME = "migrate"
  })

  secrets = local.migrate_secrets
}

# The grant task runs `acme-api grant-operator`: a person's operator
# permission, or a minted token written to its secret. It connects as the
# runtime and system logins, and its role holds one write, PutSecretValue on
# the two token secrets. The grant workflow starts it with its command as an
# override; the definition's own command only prints the usage.
module "grant" {
  source = "../task"

  name        = "grant"
  image       = var.api_image
  environment = var.environment
  command     = ["acme-api", "grant-operator", "--help"]
  policy_arns = [module.secrets.operator_tokens_policy_arn]

  environment_variables = merge(local.one_off_environment, local.api_sign_in_environment, {
    ACME_SERVICE_NAME = "grant"
  })

  secrets = local.process_secrets
}

# A stateless service rolls with one extra replica (the module's defaults).
# Before it rolls, the migrate task runs on the new image whenever the
# release brings the database something it lacks; a migration is compatible
# with the release before it (expand and contract), so the old tasks serve
# the new schema until the roll ends.
module "api" {
  source = "../service"

  name               = "api"
  image              = var.api_image
  environment        = var.environment
  cluster_arn        = module.cluster.arn
  subnet_ids         = module.network.private_subnet_ids
  security_group_ids = [module.network.app_security_group_id]
  desired_count      = var.api_desired_count
  cpu                = var.api_cpu
  memory             = var.api_memory
  port               = 8000
  metrics_port       = 8000
  target_group_arn   = module.load_balancer.target_group_arn
  policy_arns        = local.process_policies

  secrets = merge(local.process_secrets, {
    ACME_TOTP_ENCRYPTION_KEY = module.secrets.totp_encryption_key_secret_arn
    ACME_WORKOS_API_KEY      = module.secrets.workos_api_key_secret_arn
    # The API alone receives the identity provider's deliveries and checks
    # their signature; the worker handles them from the queue.
    ACME_WORKOS_WEBHOOK_SECRET = module.secrets.workos_webhook_secret_arn
    # What the portal's distribution sends in X-Acme-Edge: beside it, the
    # address CloudFront appended to X-Forwarded-For names the client.
    ACME_EDGE_SECRET = module.secrets.edge_secret_arn
  })

  environment_variables = merge(local.process_environment, local.api_sign_in_environment, {
    ACME_SERVICE_NAME           = "api"
    ACME_ADMISSION_LIMIT_READS  = tostring(local.admission_limit_reads)
    ACME_ADMISSION_LIMIT_WRITES = tostring(local.admission_limit_writes)
    ACME_HOST                   = "0.0.0.0"
    ACME_PORT                   = "8000"
    # The portal calls the API same-origin and needs none of this; a tab
    # opened before its config named the same origin still calls the API's
    # own name until it reloads, so the portal's origin stays in the list,
    # beside the other origins a browser may call from.
    ACME_CORS_ORIGINS = jsonencode(concat(["https://${var.app_domain_name}"], var.cors_origins))
    # The edge serves the API and liveness; the interactive docs are local.
    ACME_INTERACTIVE_DOCS = "false"
    # The load balancer lives in the VPC, so its X-Forwarded-For names the
    # client, or, for a request through the portal's distribution, the edge;
    # the edge secret then names the client one hop further in.
    ACME_TRUSTED_PROXIES = jsonencode([var.vpc_cidr])
  })

  health_check_command = [
    "CMD-SHELL",
    "python -c \"import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)\"",
  ]

  # The migration runs inside the apply, before the roll, whenever one of its
  # triggers differs from what the last run that succeeded recorded in the
  # state: the migration files, a new database (a replacement or a restore
  # gives it a new resource id), a password rotation, or a login's secret
  # made anew. A release that changes none of them brings nothing a database
  # lacks: the last run already applied every migration it carries, and set
  # every password it names. A run that fails leaves the record as it was, so
  # the next apply runs it again.
  pre_rollout = {
    task_definition_arn = module.migrate.task_definition_arn
    container           = module.migrate.container_name
    commands = [
      ["acme-api", "migrate", "ensure-logins"],
      ["acme-api", "migrate", "--all"],
    ]
    triggers = {
      migrations       = local.migration_fingerprint
      database         = module.database.resource_id
      password_version = tostring(var.database_password_version)
      secrets          = jsonencode(local.migrate_secrets)
    }
  }

  autoscaling = {
    enabled    = var.autoscaling_enabled && var.api_autoscaling.enabled
    max        = var.api_autoscaling.max
    target_cpu = var.api_autoscaling.target_cpu
  }
}

# A worker holds leases, so a rollout never runs more workers than desired:
# at most 100% during a deployment, and the old replica stops before its
# replacement starts. The stop timeout covers the drain. It rolls after the
# API's migration ran.
module "maintenance" {
  source = "../service"

  name               = "maintenance"
  image              = var.maintenance_image
  environment        = var.environment
  cluster_arn        = module.cluster.arn
  subnet_ids         = module.network.private_subnet_ids
  security_group_ids = [module.network.app_security_group_id]
  desired_count      = var.maintenance_desired_count
  cpu                = var.maintenance_cpu
  memory             = var.maintenance_memory
  metrics_port       = 9464
  policy_arns        = local.process_policies

  # The worker deletes a deleted account's person at WorkOS, through the
  # same Acme App the API signs people in through, with the same key.
  secrets = merge(local.process_secrets, {
    ACME_WORKOS_API_KEY = module.secrets.workos_api_key_secret_arn
  })

  environment_variables = merge(local.process_environment, {
    ACME_SERVICE_NAME      = "maintenance"
    ACME_IDENTITY_PROVIDER = "workos"
    ACME_WORKOS_CLIENT_ID  = var.workos_client_id
  })

  # The serving process answers /healthz on its metrics port from its
  # loop's last beat, held in memory; the probe boots nothing.
  health_check_command = [
    "CMD-SHELL",
    "python -c \"import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9464/healthz').status == 200 else 1)\"",
  ]

  deployment_maximum_percent         = 100
  deployment_minimum_healthy_percent = floor(100 * (var.maintenance_desired_count - 1) / var.maintenance_desired_count)
  stop_timeout_seconds               = 120
  rollout_after                      = module.api.rollout_gate

  autoscaling = {
    enabled    = var.autoscaling_enabled && var.maintenance_autoscaling.enabled
    max        = var.maintenance_autoscaling.max
    target_cpu = var.maintenance_autoscaling.target_cpu
  }
}

# What an operator reads. The dashboard is the cloud twin of the local
# Grafana one, by panel title; the alarms are the default set, to one topic.

module "dashboard" {
  source = "../dashboard"

  environment              = var.environment
  cluster_name             = module.cluster.name
  service_names            = [module.api.service_name, module.maintenance.service_name]
  database_identifier      = module.database.identifier
  load_balancer_arn_suffix = module.load_balancer.arn_suffix
  cache_node_ids           = module.cache.member_clusters
  queue_names              = module.queue.queue_names
  read_latency_routes      = module.alarms.read_latency_routes
}

module "alarms" {
  source = "../alarms"

  environment                = var.environment
  alarm_email                = var.alarm_email
  load_balancer_arn_suffix   = module.load_balancer.arn_suffix
  target_group_arn_suffix    = module.load_balancer.target_group_arn_suffix
  database_identifier        = module.database.identifier
  cluster_name               = module.cluster.name
  api_log_group_name         = module.api.log_group_name
  maintenance_log_group_name = module.maintenance.log_group_name
  queue_names                = module.queue.queue_names
  service_names              = [module.api.service_name, module.maintenance.service_name]
}
