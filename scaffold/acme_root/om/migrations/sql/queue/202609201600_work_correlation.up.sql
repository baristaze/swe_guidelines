-- The request that caused the work and its trace context, carried across the
-- queue: the claim names the first as the run's cause and the span the run
-- raises links to the second. The W3C header and not a trace id, because a
-- span links to a span. Expand only: an item enqueued before this migration
-- names no causing request, so it takes the platform's empty principal and
-- its run starts a trace of its own.

ALTER TABLE queue.work_items ADD COLUMN request_id uuid NULL;
UPDATE queue.work_items SET request_id = '00000000-0000-0000-0000-000000000000';
ALTER TABLE queue.work_items ALTER COLUMN request_id SET NOT NULL;
ALTER TABLE queue.work_items ADD COLUMN traceparent text NULL;
