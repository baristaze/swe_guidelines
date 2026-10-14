-- Gives each not-null constraint of a renamed table the name it had.

ALTER TABLE admin.system_sizes RENAME CONSTRAINT system_sizes_counted_at_not_null TO platform_sizes_counted_at_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT system_sizes_since_not_null TO platform_sizes_since_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT system_sizes_change_events_last_24h_not_null TO platform_sizes_events_last_24h_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT system_sizes_users_not_null TO platform_sizes_users_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT system_sizes_tenants_not_null TO platform_sizes_tenants_not_null;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT system_sizes_id_not_null TO platform_sizes_id_not_null;
