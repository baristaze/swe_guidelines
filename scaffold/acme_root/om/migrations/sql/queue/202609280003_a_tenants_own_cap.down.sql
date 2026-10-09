-- Takes the tenants' caps back out: the table with its policy and its
-- index. The claim with no row is the claim without them.

DROP TABLE queue.tenant_caps;
