-- Takes the history's indexes back out of the leases.

DROP INDEX core.ix_leases_org_id_resource_id_created_at_id;
DROP INDEX core.ix_leases_org_id_created_at_id;
