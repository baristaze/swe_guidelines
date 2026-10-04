# ADR 0081: The desired count is the root's

**Status**: accepted (2026-09-28)

## Context

OPS-17 (Operations, Scale-Out as a Lever): "The apply never sets the
count autoscaling owns: once a service scales, the desired count is left
to the runtime, so an apply never resets a scaled-out service." In
Terraform that means `desired_count` in the service's
`lifecycle.ignore_changes`.

Every service and worker here declares its autoscaling with its
deployment, its floor at the desired count. One variable per
environment, `autoscaling_enabled`, turns it on, and it is off by
default. With autoscaling off, an ignored count makes the root's number
a lie: a change to `desired_count` in the root applies nothing, and the
state and the file disagree with no plan that says so.

## Decision

`desired_count` is not in `ignore_changes`
(`deployment/terraform/modules/service/main.tf`, where a comment on the
service says why). The root's number is the truth, with the switch on
or off: a change to it applies.

With autoscaling on, an apply sets a scaled-out service back to its
floor, and the target-tracking policy raises it again within its
cooldown while the load is there.

## Consequences

- A deploy under load runs a few minutes at the floor. A deploy already
  rolls every task, so the dip lands where capacity is moving anyway.
- A review that reads OPS-17 against the service module finds the count
  applied and cites this record.
- An environment that runs scaled out for long stretches, where a few
  minutes at the floor would hurt, takes `desired_count` into
  `ignore_changes` and sets the floor through the autoscaling minimum
  instead.
