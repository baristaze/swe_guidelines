-- The tasks swimlane. Two lists per org: open tasks in manual order
-- (position, ascending) and done tasks newest first (updated_at, then id).
-- Each list has its compound index, so org_id gets no index of its own.

CREATE TABLE core.tasks (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    deleted_at timestamptz NULL,
    deleted_by uuid NULL,
    title text NOT NULL,
    notes text NOT NULL,
    status text NOT NULL,
    assignee_id uuid NULL,
    position double precision NOT NULL,
    CONSTRAINT pk_tasks PRIMARY KEY (id)
);
CREATE INDEX ix_tasks_org_id_status_position ON core.tasks (org_id, status, position);
CREATE INDEX ix_tasks_org_id_status_updated_at_id ON core.tasks (org_id, status, updated_at, id);
