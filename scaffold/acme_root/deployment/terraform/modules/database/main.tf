# One Postgres instance holds every database role. The master password
# arrives as an ephemeral value and is written write-only, so neither the
# state nor a saved plan holds it; it reaches the migrate task alone, through
# the secret the secrets module writes the same way.

locals {
  tags = { "acme:environment" = var.environment }
}

resource "aws_db_subnet_group" "this" {
  name       = "acme-${var.environment}"
  subnet_ids = var.subnet_ids
  tags       = local.tags
}

# The instance refuses a connection without TLS. Postgres 18 on RDS already
# defaults to it; declared here, a default that moves cannot undo it.
resource "aws_db_parameter_group" "this" {
  name   = "acme-${var.environment}"
  family = "postgres${var.engine_version}"
  tags   = local.tags

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier     = "acme-${var.environment}"
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.allocated_storage * 5
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name = "acme"
  # The master user. On RDS it is not a superuser and carries no BYPASSRLS:
  # it is `rds_superuser`. The application does not connect as it: the
  # migrate task's `ensure-logins` uses it to create the three logins
  # (acme_migration, acme_runtime, acme_system) and move the schema to the
  # migration login, and every serving task connects as the runtime and
  # system logins, each under row-level security.
  username            = "acme"
  password_wo         = var.master_password
  password_wo_version = var.master_password_version
  port                = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  parameter_group_name   = aws_db_parameter_group.this.name
  vpc_security_group_ids = var.security_group_ids
  publicly_accessible    = false
  multi_az               = var.multi_az

  backup_retention_period    = var.backup_retention_days
  auto_minor_version_upgrade = true
  # A major upgrade is a change of its own, never a side effect of an apply:
  # the pull request that raises engine_version turns this on with it.
  allow_major_version_upgrade = false
  # `destroyable` is the nuke's flag, false everywhere but on the apply
  # before the destroy. In staging it lifts the protection. Production's
  # protection comes off only through a released change, never through the
  # flag, and production keeps its final snapshot and its automated backups
  # whatever the flag says: a destroyed production is still restorable. Staging skips both on the way
  # down, so a rebuild finds no snapshot name taken.
  deletion_protection       = var.deletion_protection && !(var.destroyable && var.environment != "production")
  skip_final_snapshot       = var.destroyable && var.environment != "production"
  final_snapshot_identifier = "acme-${var.environment}-final"
  delete_automated_backups  = var.environment != "production"
  copy_tags_to_snapshot     = true

  tags = local.tags
}
