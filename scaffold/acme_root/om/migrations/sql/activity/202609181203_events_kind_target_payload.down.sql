ALTER TABLE activity.events DROP COLUMN payload;
ALTER TABLE activity.events DROP COLUMN app;
ALTER TABLE activity.events ADD COLUMN idempotency_key uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE activity.events ALTER COLUMN idempotency_key DROP DEFAULT;
ALTER TABLE activity.events ADD COLUMN action text NOT NULL DEFAULT 'updated';
ALTER TABLE activity.events ALTER COLUMN action DROP DEFAULT;
UPDATE activity.events SET action = split_part(kind, '.', 3), kind = split_part(kind, '.', 2);
ALTER TABLE activity.events RENAME COLUMN target_id TO entity_id;
ALTER TABLE activity.events RENAME COLUMN kind TO entity;
