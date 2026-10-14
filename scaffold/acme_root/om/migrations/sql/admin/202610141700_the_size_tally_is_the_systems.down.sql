-- Gives the size tally its earlier name back, its row and its key kept.

ALTER TABLE admin.system_sizes RENAME CONSTRAINT pk_system_sizes TO pk_platform_sizes;
ALTER TABLE admin.system_sizes RENAME TO platform_sizes;
