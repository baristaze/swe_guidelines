-- Takes the tenants' caps back out: the table with its policies and its
-- index. The claim with no row is the claim without them.

DROP TABLE queue.tenant_caps;
