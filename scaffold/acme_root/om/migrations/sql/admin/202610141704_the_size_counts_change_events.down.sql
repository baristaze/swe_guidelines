-- Gives the size tally's column its earlier name back, its row kept.

ALTER TABLE admin.system_sizes RENAME COLUMN change_events_last_24h TO events_last_24h;
