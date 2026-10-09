-- A lease kept by the worker that runs its job: the key of the work item a
-- grant writes in its own commit, when what it starts is a job, and when that
-- job started; and on a request, the window its job has to start in. Each is
-- nullable, so a lease its holder keeps itself, and every row before this
-- one, reads as it did.

ALTER TABLE core.leases ADD COLUMN job_key uuid NULL;
ALTER TABLE core.leases ADD COLUMN started_at timestamptz NULL;
ALTER TABLE core.lease_requests ADD COLUMN start_seconds integer NULL;
