-- The read by id (the idempotent append) is served by the primary key, and the
-- stream is read by seq; the (org_id, id) index served no query.

DROP INDEX activity.ix_events_org_id_id;
