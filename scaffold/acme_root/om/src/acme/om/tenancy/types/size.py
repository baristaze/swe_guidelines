"""The platform's size, what the first responder to an alarm reads before it
escalates: an alarm on a platform of one tenant and one user is the
developer at work.

A read model. The maintenance worker's sweep counts it across every tenant
and keeps the latest count as the platform's one tally row, in the `admin`
role; the operator plane reads that row and never counts (ADR 0074). The
row is derived, and the next count rebuilds it."""

from datetime import datetime

from acme.om.base import Platform


class PlatformSize(Platform):
    tenants: int  # live orgs
    users: int  # live users across every tenant; a person in two orgs counts twice
    tasks_last_24h: int  # tasks created in the window, whatever became of them since
    events_last_24h: int  # events produced in the window, every tenant's stream
    since: datetime  # where the window starts; it ends at `counted_at`
    counted_at: datetime  # when the sweep counted it: the answer is as old as this
