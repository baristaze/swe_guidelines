-- Reverses the second fence: the policy goes, the table stops forcing and
-- stops enforcing row-level security, and the predicate in the query is the
-- only fence again.

DROP POLICY tenant_fence ON activity.events;
ALTER TABLE activity.events NO FORCE ROW LEVEL SECURITY;
ALTER TABLE activity.events DISABLE ROW LEVEL SECURITY;

DROP POLICY tenant_fence ON activity.event_cursors;
ALTER TABLE activity.event_cursors NO FORCE ROW LEVEL SECURITY;
ALTER TABLE activity.event_cursors DISABLE ROW LEVEL SECURITY;
