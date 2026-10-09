-- Takes the job's columns back out of the leases and their requests.

ALTER TABLE core.lease_requests DROP COLUMN start_seconds;
ALTER TABLE core.leases DROP COLUMN started_at;
ALTER TABLE core.leases DROP COLUMN job_key;
