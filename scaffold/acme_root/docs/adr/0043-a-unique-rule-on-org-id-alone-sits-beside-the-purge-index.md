# ADR 0043: A unique rule on org_id alone sits beside the purge's index

**Status**: superseded (2026-09-26). Guideline v0.35.0 fixes STO-14:
it no longer flags a unique index on `org_id` beside a compound index
that `org_id` leads, because a unique index is a rule, one row per
tenant, and not a second lookup. Both indexes stay, as this record
decided. The exception for STO-14 leaves `pyproject.toml`, and
`arch-check` passes without it. The record stays for the interval it
covers.

## Context

A Slack installation is soft-deleted. An org holds one living
installation at a time, so `core.slack_installations` carries
`uq_slack_installations_org_id`: unique on `org_id`, over the rows
where `deleted_at IS NULL`. That is the partial unique index STO-26
asks of a soft-deletable table.

The sweep reads the other rows. Its purge deletes a tenant's
installations uninstalled before the retention (`org_id = X AND
deleted_at < Y`), and the tenant purge deletes all of a tenant's
installations (`org_id = X`). Neither can use an index that holds only
the living rows, so each one read the whole table, for every tenant, on
every pass. The table gains `ix_slack_installations_org_id_deleted_at`
on `(org_id, deleted_at)`, the index users and memberships carry for
the same two reads.

STO-14 says a column that leads a compound index gets no single-column
index of its own. `arch-check` reads the unique index as one: an index
on `org_id` alone, beside a compound index that `org_id` leads.

## Decision

Both indexes stay. The unique index is a rule, not a lookup: it holds
one living installation per org, and the compound index cannot hold
it. The compound index serves the reads of the rows the unique index
leaves out. The redundancy STO-14 guards against is a plain `org_id`
index that the compound one makes useless, and neither index here is
that.

## Consequences

The exception in `pyproject.toml` names this record, for STO-14 on
`om/src/acme/om/slack/storage/tables/slack.py`. A plain `org_id`
index added to that table later is the violation the rule means, and
the exception would hide it; the table's comment says why each index
is there, so a reader sees what belongs.

The guideline's next release stops reading a unique `org_id` index as
the one STO-14 forbids. The exception, and this record, go when Acme
pins that release.
