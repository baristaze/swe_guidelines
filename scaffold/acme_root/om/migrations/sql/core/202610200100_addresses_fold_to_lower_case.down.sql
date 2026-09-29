-- The digest goes back to the address as stored, which the release before
-- computes. The addresses stay folded: the case they had is gone, and the
-- release before finds a folded address by its folded spelling.

ALTER TABLE core.identities ALTER COLUMN email_digest
    SET EXPRESSION AS (encode(sha256(decode(email, 'escape')), 'hex'));
