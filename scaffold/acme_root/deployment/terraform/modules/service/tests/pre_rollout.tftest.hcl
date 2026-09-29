# The pre-rollout run (the API's migration) is keyed on the triggers its
# caller names and on its commands, never on the image: a release that brings
# the database nothing new rolls without a one-off task. Runs offline:
# `terraform test` in this folder.

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
  environment        = "test"
  cluster_arn        = "arn:aws:ecs:us-east-1:123456789012:cluster/acme-test"
  subnet_ids         = ["subnet-1"]
  security_group_ids = ["sg-1"]
  metrics_port       = 8000
  autoscaling        = { enabled = false, max = 1, target_cpu = 60 }
  pre_rollout = {
    task_definition_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/acme-test-migrate:7"
    container           = "migrate"
    commands            = [["acme-api", "migrate", "ensure-logins"], ["acme-api", "migrate", "--all"]]
    triggers = {
      migrations       = "fingerprint-of-the-migration-files"
      database         = "db-ABCDEFGHIJKLMNOP"
      password_version = "1"
    }
  }
}

run "the_triggers_and_the_commands_key_the_run" {
  command = plan

  variables {
    image = "123456789012.dkr.ecr.us-east-1.amazonaws.com/acme-api@sha256:1111111111111111111111111111111111111111111111111111111111111111"
  }

  assert {
    condition = jsonencode(terraform_data.pre_rollout[0].triggers_replace) == jsonencode([
      {
        migrations       = "fingerprint-of-the-migration-files"
        database         = "db-ABCDEFGHIJKLMNOP"
        password_version = "1"
      },
      [["acme-api", "migrate", "ensure-logins"], ["acme-api", "migrate", "--all"]],
    ])
    error_message = "with triggers named, the run is keyed on them and the commands alone, so a new image by itself runs nothing"
  }
}

run "a_new_image_keys_the_run_the_same" {
  command = plan

  variables {
    image = "123456789012.dkr.ecr.us-east-1.amazonaws.com/acme-api@sha256:2222222222222222222222222222222222222222222222222222222222222222"
  }

  assert {
    condition = jsonencode(terraform_data.pre_rollout[0].triggers_replace) == jsonencode([
      {
        migrations       = "fingerprint-of-the-migration-files"
        database         = "db-ABCDEFGHIJKLMNOP"
        password_version = "1"
      },
      [["acme-api", "migrate", "ensure-logins"], ["acme-api", "migrate", "--all"]],
    ])
    error_message = "a release that changes only the image keys the pre-rollout run as the one before it did"
  }
}
