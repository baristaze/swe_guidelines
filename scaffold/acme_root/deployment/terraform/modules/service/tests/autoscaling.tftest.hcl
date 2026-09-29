# The autoscaling switch: off declares nothing, on declares a target whose
# floor is the desired count and a target-tracking policy on CPU at the
# number the caller passes. Runs offline: `terraform test` in this folder.

# The account, partition, and region the module reads have to look real: the
# task role's permissions boundary is an ARN the provider checks the shape
# of, and a policy document has to be a JSON object.
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
  desired_count      = 2
  metrics_port       = 8000
}

run "off_declares_nothing" {
  command = plan

  variables {
    autoscaling = { enabled = false, max = 6, target_cpu = 60 }
  }

  assert {
    condition     = length(aws_appautoscaling_target.this) == 0 && length(aws_appautoscaling_policy.cpu) == 0
    error_message = "with the switch off there must be no scaling target and no policy"
  }
}

run "on_scales_between_desired_and_max_on_cpu" {
  command = plan

  variables {
    autoscaling = { enabled = true, max = 6, target_cpu = 55 }
  }

  assert {
    condition     = aws_appautoscaling_target.this[0].min_capacity == 2 && aws_appautoscaling_target.this[0].max_capacity == 6
    error_message = "the floor is desired_count and the ceiling is autoscaling.max"
  }

  assert {
    condition     = aws_appautoscaling_target.this[0].resource_id == "service/acme-test/api"
    error_message = "the target names the service by cluster name and service name"
  }

  assert {
    condition     = aws_appautoscaling_policy.cpu[0].policy_type == "TargetTrackingScaling"
    error_message = "the policy is target tracking, not step scaling"
  }

  assert {
    condition     = aws_appautoscaling_policy.cpu[0].target_tracking_scaling_policy_configuration[0].target_value == 55
    error_message = "the policy tracks the CPU number the caller passes"
  }

  assert {
    condition     = aws_appautoscaling_policy.cpu[0].target_tracking_scaling_policy_configuration[0].predefined_metric_specification[0].predefined_metric_type == "ECSServiceAverageCPUUtilization"
    error_message = "the policy tracks the service's average CPU"
  }
}

run "refuses_a_ceiling_below_the_floor" {
  command = plan

  variables {
    autoscaling = { enabled = true, max = 1, target_cpu = 60 }
  }

  expect_failures = [var.autoscaling]
}
