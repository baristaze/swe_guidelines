# Scale: one variable turns the environment's autoscaling on

Scaling out is a deployment decision, and this is the one place it is
made. Every service and worker declares its autoscaling with its
deployment; one root variable per environment turns all of it on, and
it is off by default because an unattended scale-out is a bill nobody
approved.

## The one flip

In `deployment/terraform/environments/<staging|prod>/main.tf`:

```hcl
  autoscaling_enabled     = false
  api_autoscaling         = { max = 2 }
  maintenance_autoscaling = { max = 1 }
```

A pull request that sets `autoscaling_enabled = true` scales the
environment. Nothing else needs to change: every lever below the flip
is declared on (`enabled = true` by default in each service's object),
with its ceiling (`max`) and the CPU it tracks (`target_cpu`, 60
percent by default). Production's ceilings are 3 for the API and 1 for
the maintenance worker; staging's are 2 and 1. Each size in
[../../deployment/cloud/README.md](../../deployment/cloud/README.md)
names its ceilings and a pool that holds at them, so the flip is safe
at every size. One service can stay
out with `enabled = false` in its object; the flip still governs the
rest.

The floor is the desired count already in the root
(`api_desired_count`, `maintenance_desired_count`), so turning the flip
on changes nothing until load does.

## What turns on

Per service, in `deployment/terraform/modules/service`:

- an Application Auto Scaling target on the ECS service's desired
  count, `min = desired_count`, `max = <the ceiling>`;
- a target-tracking policy `acme-<environment>-<service>-cpu` on
  `ECSServiceAverageCPUUtilization` at `target_cpu`, scaling out after
  a 60 second cooldown and in after 300, so a burst is answered in a
  minute and a lull has to last five before a task is taken away.

With the flip off, neither exists: `terraform plan` on the flip shows
exactly two resources added per service, and nothing changed.

The database's storage grows on its own from the first apply
(`max_allocated_storage` is five times the allocation), flip or no
flip: that is a ceiling on how full a disk gets, not a bill that scales
with traffic, and a full disk is an outage.

## A deploy resets the count to the floor

`desired_count` is not in the service's `ignore_changes`, on purpose.
With the flip on, every apply sets the running count back to the
floor, and the policy raises it again within its cooldown while load is
still there. A deploy is already a roll, and a few minutes at the floor
is the price of the root's number staying the truth: a change to
`api_desired_count` applies, flip on or off, and nothing in the state
disagrees with the file. An environment that cannot afford the dip
raises its floor in the same pull request.

## How to read that it happened

Under the investigate profile ([operate.md](operate.md)):

```bash
export AWS_PROFILE=acme-staging-investigate

# The target and the policy exist, with the floor and the ceiling.
aws application-autoscaling describe-scalable-targets --service-namespace ecs \
  --resource-ids service/acme-staging/api service/acme-staging/maintenance \
  --query 'ScalableTargets[].{service:ResourceId,min:MinCapacity,max:MaxCapacity}' --output table
aws application-autoscaling describe-scaling-policies --service-namespace ecs \
  --resource-id service/acme-staging/api \
  --query 'ScalingPolicies[].{name:PolicyName,target:TargetTrackingScalingPolicyConfiguration.TargetValue}'

# What it did, most recent first: each activity names the alarm that
# fired, the count before, and the count after.
aws application-autoscaling describe-scaling-activities --service-namespace ecs \
  --resource-id service/acme-staging/api --max-results 10 \
  --query 'ScalingActivities[].{at:StartTime,what:Description,why:Cause}' --output table

# Where the service is now.
aws ecs describe-services --cluster acme-staging --services api \
  --query 'services[0].{desired:desiredCount,running:runningCount}'
```

On the dashboard, "Targets up" and "Running tasks" show the count move;
the CPU the policy tracks is in the ECS console's service metrics and
in `aws cloudwatch get-metric-statistics --namespace AWS/ECS
--metric-name CPUUtilization --dimensions Name=ClusterName,Value=acme-staging
Name=ServiceName,Value=api`. The `acme-staging-api-tasks-below-desired`
alarm reads desired against running, so a scale-out (desired rises,
running follows) reads the same as a deploy and does not fire on its
own.

## What it costs

A scale-out is tasks, and tasks are the bill. The ceiling is the most
an environment can spend on a service at once; its account's budget,
declared by the bootstrap root, is the catch-all under it, and its 80 percent notification is the one
to read when a flip has been on for a while.
