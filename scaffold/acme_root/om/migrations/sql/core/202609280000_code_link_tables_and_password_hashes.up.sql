-- The contract half of two expands.
--
-- The channel linked by a one-time code gave way to the installation
-- (202609261701). No release that serves reads or writes its two tables, so
-- both go, with the rows they hold. A code-linked channel holds no token, so
-- nothing in them is carried anywhere.
--
-- Acme keeps no password (202609261200). No release that serves reads
-- `identities.password_hash`, so the hashes go now. The column itself stays
-- one more release: the release before this one names it, as NULL, in every
-- identity it inserts, and a migration runs before the services roll.

DROP TABLE core.slack_link_codes;
DROP TABLE core.slack_connections;

UPDATE core.identities SET password_hash = NULL WHERE password_hash IS NOT NULL;
