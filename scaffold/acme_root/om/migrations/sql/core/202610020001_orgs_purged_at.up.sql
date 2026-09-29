-- When the sweep found nothing left of a deleted tenant to trim; it leaves
-- the tenant out from then on. Expand only: the column is nullable, and the
-- release before this one neither reads nor writes it, so it keeps sweeping
-- every tenant while a rollout runs both.
ALTER TABLE core.orgs ADD COLUMN purged_at timestamptz NULL;
