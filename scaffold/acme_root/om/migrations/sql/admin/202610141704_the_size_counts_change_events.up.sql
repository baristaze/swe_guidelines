-- The size tally counts change events: its column takes their name, its
-- one row kept.

ALTER TABLE admin.system_sizes RENAME COLUMN events_last_24h TO change_events_last_24h;
