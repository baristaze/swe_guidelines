-- The floor is the highest seq the trim has removed from a tenant's stream,
-- 0 while it has removed none. Every event above it, up to the head, is
-- stored. A read that starts below it is refused, because the events it
-- asks for are gone (ADR 0040).
--
-- Expand-only. The release before inserts a cursor row naming `org_id` and
-- `head`, and the default fills the floor.

ALTER TABLE activity.event_cursors ADD COLUMN floor bigint NOT NULL DEFAULT 0;

-- The operator's size counts the events produced in the last day, across
-- every tenant. With no index that count reads the whole stream. A b-tree,
-- not a BRIN: the trim frees pages at the bottom of the heap, new events
-- land there, and the physical order stops following the time.

CREATE INDEX ix_events_produced_at ON activity.events (produced_at);
