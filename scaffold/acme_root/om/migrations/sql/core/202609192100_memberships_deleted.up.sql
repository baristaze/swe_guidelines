-- A removed member's membership ends with them: it is soft-deleted beside
-- the user, so no read lists it and no role change reaches it during the
-- retention period. Expand only; the columns are nullable and every row
-- written before this is a live membership.

ALTER TABLE core.memberships ADD COLUMN deleted_at timestamptz NULL;
ALTER TABLE core.memberships ADD COLUMN deleted_by uuid NULL;
