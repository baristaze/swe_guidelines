-- The size tally takes the system's name. A rename keeps the one row, its
-- key, and the serving logins' grants, which follow the table.

ALTER TABLE admin.platform_sizes RENAME TO system_sizes;
ALTER TABLE admin.system_sizes RENAME CONSTRAINT pk_platform_sizes TO pk_system_sizes;
