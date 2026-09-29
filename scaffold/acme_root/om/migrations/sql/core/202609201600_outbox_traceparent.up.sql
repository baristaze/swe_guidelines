-- The trace context of the request that made the write, carried on the row
-- the write announces itself with, so the span on the far side of a handoff
-- links to the trace that caused it. The W3C header and not a trace id: an
-- id names a trace, and only the header carries what a span links to.
-- Expand only: a row written before this migration carries no header, and
-- the relayed work item then starts a trace of its own.

ALTER TABLE core.outbox_rows ADD COLUMN traceparent text NULL;
