# One container cluster per environment; services and workers run on the
# same runtime.

resource "aws_ecs_cluster" "this" {
  name = "acme-${var.environment}"
  tags = { "acme:environment" = var.environment }

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}
