-- Gives the stream and its cursors their earlier names back, every row kept;
-- the fence, the grants, and the indexes follow the tables.

ALTER TABLE activity.change_event_cursors
    RENAME CONSTRAINT pk_change_event_cursors TO pk_event_cursors;
ALTER TABLE activity.change_event_cursors RENAME TO event_cursors;
ALTER INDEX activity.ix_change_events_produced_at RENAME TO ix_events_produced_at;
ALTER INDEX activity.uq_change_events_org_id_seq RENAME TO uq_events_org_id_seq;
ALTER TABLE activity.change_events RENAME CONSTRAINT pk_change_events TO pk_events;
ALTER TABLE activity.change_events RENAME TO events;
