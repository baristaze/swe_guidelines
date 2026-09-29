-- Every claim mints a token the claim returns, and every transition of a
-- claimed item conditions on it rather than on the worker's name: one worker
-- can hold one item twice across a requeue. Expand only: a row claimed before
-- this migration carries no token, and its holder is refused at the next
-- transition the way a lost lease is, then the sweep requeues it.

ALTER TABLE queue.work_items ADD COLUMN claim_token uuid NULL;
