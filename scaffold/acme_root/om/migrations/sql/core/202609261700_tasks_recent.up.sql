-- The open tasks newest first: Slack's short list of the newest ten reads
-- the org's open tasks by id, whose time is when each was made.
CREATE INDEX ix_tasks_org_id_status_id ON core.tasks (org_id, status, id);
