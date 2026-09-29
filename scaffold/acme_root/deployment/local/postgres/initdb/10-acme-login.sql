-- The local master, and the reason it is not the superuser.
--
-- A superuser walks past every row-level security policy, and so does a role
-- with BYPASSRLS, so a fence behind one is a drawing. The container's
-- superuser is `postgres`, and nothing of Acme connects as it. `acme` plays
-- the part the master user plays in the cloud: it owns the database, it may
-- create roles, and it carries neither attribute.
--
-- The master opens one command, `migrate ensure-logins`, which `make migrate`
-- runs first. It makes the three logins the processes connect as: the
-- migration login, which owns the schemas and runs the migrations; the
-- runtime login, which every request uses; and the system login, which only
-- the system scope uses. None of them is a superuser or carries BYPASSRLS.
--
-- This runs once, on an empty data directory. A stack that was up before
-- `acme` could create roles keeps the old login until `make reset`, or until
-- `ALTER ROLE acme CREATEROLE` as `postgres`.

CREATE ROLE acme LOGIN PASSWORD 'acme' NOSUPERUSER NOBYPASSRLS NOCREATEDB CREATEROLE;

ALTER DATABASE acme OWNER TO acme;
ALTER SCHEMA public OWNER TO acme;
