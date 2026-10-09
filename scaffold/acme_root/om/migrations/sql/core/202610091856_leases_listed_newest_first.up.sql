-- The history of the leases, newest first: the org's, and one resource's,
-- each read by an index whose order it walks backwards and stops at the
-- page's bound. No column changes, so every row reads as it did.

CREATE INDEX ix_leases_org_id_created_at_id ON core.leases (org_id, created_at, id);
CREATE INDEX ix_leases_org_id_resource_id_created_at_id
    ON core.leases (org_id, resource_id, created_at, id);
