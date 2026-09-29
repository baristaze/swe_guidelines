# Runbooks

One file per procedure a person follows by hand: what to check, what to
run, and what to expect. Each names the setting or the table it touches,
and the ADR that says why it has that shape.

- [deploy.md](deploy.md): staging is `main`, production is `release`;
  create an environment, grant an operator, cut a release, approve it,
  roll it back, and what to do when a step fails.
- [operate.md](operate.md): the profile an investigation runs under, the
  dashboard, the alarms, a client answered 429, a failed work item, and
  the costs.
- [operator.md](operator.md): how a person gets onto the operator plane,
  and how an operator's tokens end.
- [scale.md](scale.md): the one variable that scales an environment, and
  how to read that it did.
- [restore.md](restore.md): what is backed up, a restore to a new
  instance, the rehearsal, and relaying the outbox again.
- [support.md](support.md): how a support investigation runs, from a
  tenant's message to a finding.
- [tenant-isolation.md](tenant-isolation.md): the cross-tenant cases,
  and the two controls that say they catch a missing fence.
- [providers/workos.md](providers/workos.md): the identity provider: its
  environments, the application and its key, the redirects, the webhook,
  and what breaks.
