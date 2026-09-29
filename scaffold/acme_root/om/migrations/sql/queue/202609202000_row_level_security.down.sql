-- Reverses the second fence: the policy goes, the table stops forcing and
-- stops enforcing row-level security, and the predicate in the query is the
-- only fence again.

DROP POLICY tenant_fence ON queue.work_items;
ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
ALTER TABLE queue.work_items DISABLE ROW LEVEL SECURITY;
