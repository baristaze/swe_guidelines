-- The queue's fence, as one policy per login (ADR 0044). The two halves of
-- the one policy become two policies, each bound to the login it admits:
--
-- - tenant_fence, TO acme_runtime: the rows of the tenant the transaction
--   names, and nothing else. It carries no system-scope clause, so the
--   runtime login naming the system scope reads nothing, as before.
-- - system_fence, TO acme_system: every row, when the transaction names the
--   system scope, the empty UUID; nothing when it names none.
--
-- A policy applies only to the logins it names, and a login no policy names
-- reads nothing, since FORCE holds the owner too. So the migration login and
-- the master read no work item under any setting.
--
-- Why: Postgres picks the policies by login when it plans. The system login's
-- claim sees one plain predicate on the setting, not an OR with a tenant
-- comparison the planner estimates at one tenant's share of the rows, so it
-- walks the claim's index in order and stops at the first free row instead
-- of sorting the whole ready backlog.

DROP POLICY tenant_fence ON queue.work_items;
CREATE POLICY tenant_fence ON queue.work_items FOR ALL TO acme_runtime
    USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
    WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
CREATE POLICY system_fence ON queue.work_items FOR ALL TO acme_system
    USING (current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000')
    WITH CHECK (current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000');
