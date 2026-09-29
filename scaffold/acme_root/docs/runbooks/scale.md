# Scale: one variable turns autoscaling on

Scaling out is a deployment decision, made in one place. Every service
declares its autoscaling, and one variable per environment turns all of
it on. It is off by default, because an unattended scale-out is a bill
nobody approved.

## The flip

In `deployment/terraform/environments/<staging|prod>/main.tf`:

```hcl
  autoscaling_enabled     = false
  api_autoscaling         = { max = 2 }
  maintenance_autoscaling = { max = 1 }
```

A pull request that sets `autoscaling_enabled = true` scales the
environment. Each service's object is on by default, with its ceiling
(`max`) and the CPU it tracks (`target_cpu`, 60 percent). One service
stays out with `enabled = false` in its object. The floor is the
desired count already in the root (`api_desired_count`,
`maintenance_desired_count`), so the flip changes nothing until load
does. The sizes in
[deployment/cloud/README.md](../../deployment/cloud/README.md) name a
database pool that holds at every ceiling.

## What turns on

Per service, in `deployment/terraform/modules/service`:

- a scaling target on the service's desired count, from the floor to the
  ceiling;
- a target-tracking policy, `acme-<environment>-<service>-cpu`, that
  scales out after a 60-second cooldown and in after 300.

With the flip off, neither exists. The plan of the flip adds two
resources per service and changes nothing else.

## An apply returns the count to the floor

`desired_count` stays out of `ignore_changes`, so every apply sets a
scaled service back to its floor, and the policy raises it again within
its cooldown while load lasts
([ADR 0081](../adr/0081-the-desired-count-is-the-roots.md)). An
environment that cannot afford the dip raises its floor in the same pull
request.

## Read that it happened

Under the investigate profile ([operate.md](operate.md)):

```bash
export AWS_PROFILE=acme-staging-investigate
aws application-autoscaling describe-scalable-targets --service-namespace ecs \
  --resource-ids service/acme-staging/api service/acme-staging/maintenance \
  --query 'ScalableTargets[].{service:ResourceId,min:MinCapacity,max:MaxCapacity}' --output table
aws application-autoscaling describe-scaling-activities --service-namespace ecs \
  --resource-id service/acme-staging/api --max-results 10 \
  --query 'ScalingActivities[].{at:StartTime,what:Description,why:Cause}' --output table
aws ecs describe-services --cluster acme-staging --services api \
  --query 'services[0].{desired:desiredCount,running:runningCount}'
```

On the dashboard, "Targets up" and "Running tasks" show the count move.
The tasks-below-desired alarm compares desired with running, so a
scale-out does not trip it.

## What it costs

A scale-out is tasks, and tasks are the bill. The ceiling is the most an
environment spends on a service at once. The account's budget is the
catch-all under it: read its 80 percent notice when the flip has been on
for a while.

## When it fails

- **No scaling target after the flip.** The service's object carries
  `enabled = false`, or the apply did not run. Read the plan.
- **The count never rises.** The CPU stays under `target_cpu`: the load
  is elsewhere, often the database.
