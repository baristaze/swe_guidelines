-- The identity provider's own session behind a Acme session: the sign-out
-- ends it too. Expand only: the column is nullable, and the release before
-- this one neither reads nor writes it, so it keeps working on it while a
-- rollout runs both. A row that release writes carries none, and its
-- sign-out ends Acme's session alone, as it did. Nothing is contracted.

ALTER TABLE core.sessions ADD COLUMN provider_session_id text NULL;
