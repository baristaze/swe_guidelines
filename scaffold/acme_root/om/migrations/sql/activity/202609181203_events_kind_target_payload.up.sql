-- An event is (kind, target_id, payload) plus its provenance: the kind folds
-- the entity, its namespace, and the action into one name, and the payload is
-- the record's snapshot. The relay's idempotency key is the event id itself,
-- so the separate key column goes. An audit entry is the same shape plus the
-- principal and the app, so the app joins the actor and the request. A
-- one-step rename, not expand and contract: ADR 0006.

ALTER TABLE activity.events RENAME COLUMN entity TO kind;
ALTER TABLE activity.events RENAME COLUMN entity_id TO target_id;
UPDATE activity.events SET kind = (CASE kind WHEN 'task' THEN 'tasks' ELSE 'tenancy' END) || '.' || kind || '.' || action;
ALTER TABLE activity.events DROP COLUMN action;
ALTER TABLE activity.events DROP COLUMN idempotency_key;
ALTER TABLE activity.events ADD COLUMN app text NOT NULL DEFAULT 'api';
ALTER TABLE activity.events ALTER COLUMN app DROP DEFAULT;
ALTER TABLE activity.events ADD COLUMN payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE activity.events ALTER COLUMN payload DROP DEFAULT;
