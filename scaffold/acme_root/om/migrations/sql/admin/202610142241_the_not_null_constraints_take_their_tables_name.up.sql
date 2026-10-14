-- Each not-null constraint of a renamed table takes the name Postgres gives
-- it on that table: <table>_<column>_not_null.

ALTER TABLE admin.system_sizes RENAME CONSTRAINT platform_sizes_id_not_null TO system_sizes_id_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT platform_sizes_tenants_not_null TO system_sizes_tenants_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT platform_sizes_users_not_null TO system_sizes_users_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT platform_sizes_events_last_24h_not_null TO system_sizes_change_events_last_24h_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT platform_sizes_since_not_null TO system_sizes_since_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT platform_sizes_counted_at_not_null TO system_sizes_counted_at_not_null;
