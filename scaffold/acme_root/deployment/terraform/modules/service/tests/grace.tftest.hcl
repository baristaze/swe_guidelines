# The health-check grace period: a service behind a target group gets one
# that covers a task's start on a fresh Fargate host; a worker, with no
# target group, gets none. Runs offline: `terraform test` in this folder.

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1" }
  }
}

variables {
  name               = "api"
  image              = "123456789012.dkr.ecr.us-east-1.amazonaws.com/acme-api@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  environment        = "test"
  cluster_arn        = "arn:aws:ecs:us-east-1:123456789012:cluster/acme-test"
  subnet_ids         = ["subnet-1"]
  security_group_ids = ["sg-1"]
  metrics_port       = 8000
  autoscaling        = { enabled = false, max = 1, target_cpu = 60 }
}

run "a_service_behind_a_target_group_waits_out_a_cold_start" {
  command = plan

  variables {
    port             = 8000
    target_group_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/acme-test-api/0123456789abcdef"
  }

  assert {
    condition     = aws_ecs_service.this.health_check_grace_period_seconds == 150
    error_message = "the grace covers the slowest observed start (about 90 seconds) plus two passing checks, with room to spare"
  }
}

run "a_worker_has_no_grace" {
  command = plan

  variables {
    name = "maintenance"
  }

  assert {
    condition     = aws_ecs_service.this.health_check_grace_period_seconds == null || aws_ecs_service.this.health_check_grace_period_seconds == 0
    error_message = "a service with no target group has no load balancer verdict to wait out"
  }
}
