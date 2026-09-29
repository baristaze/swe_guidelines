# Valkey serves every cache scope and the topic bus (pub/sub). In transit and
# at rest encryption are on, so the URL is valkeys://.

locals {
  tags = { "acme:environment" = var.environment }
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "acme-${var.environment}"
  subnet_ids = var.subnet_ids
  tags       = local.tags
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "acme-${var.environment}"
  description          = "Acme ${var.environment}: cache scopes and the topic bus"

  engine               = "valkey"
  engine_version       = var.engine_version
  parameter_group_name = "default.valkey9"
  node_type            = var.node_type
  num_cache_clusters   = var.node_count
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = var.security_group_ids

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  automatic_failover_enabled = var.node_count > 1
  multi_az_enabled           = var.node_count > 1
  apply_immediately          = true

  tags = local.tags
}
