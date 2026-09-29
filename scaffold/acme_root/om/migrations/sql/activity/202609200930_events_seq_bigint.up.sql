-- `activity.events.seq` and `activity.event_cursors.head` hold the same
-- number, the tenant's position in its stream, and the cursor's is a bigint.
-- The stream's was an integer, so a tenant that passed 2^31 events would have
-- a head the append could no longer write beside it. Widening is expand-only:
-- the release before this one reads and writes the column unchanged.

ALTER TABLE activity.events ALTER COLUMN seq TYPE bigint;
