"""The TOTP secret at rest: sealed with AES-256-GCM under the process's TOTP
key, a process credential from the secret store (`totp_encryption_key`), so
a copy of the identities table is no authenticator. The key has the shape
of a Fernet key, URL-safe base64 of 32 random bytes, and those 32 bytes are
the AES key. A sealed secret is `v1.<nonce>.<ciphertext>`, both base64url,
and the identity's id is the associated data, so a sealed secret copied
onto another identity does not open."""

import base64
import binascii
import secrets
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from acme.om.exceptions import Unavailable

TOTP_SECRET_BYTES = 20
"""160 bits, the length RFC 4226 recommends for an HMAC-SHA1 secret."""
KEY_BYTES = 32
VERSION = "v1"


def new_totp_secret() -> bytes:
    return secrets.token_bytes(TOTP_SECRET_BYTES)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def key_bytes(key: str) -> bytes:
    """The 32 bytes a Fernet-shaped key encodes; anything else is refused at
    boot, so a mistyped key never seals a secret nobody can open later."""
    try:
        raw = base64.urlsafe_b64decode(key.encode())
    except binascii.Error, ValueError:
        raw = b""
    if len(raw) != KEY_BYTES:
        raise ValueError("the TOTP encryption key is URL-safe base64 of 32 bytes (a Fernet key)")
    return raw


def new_key() -> str:
    """A key of the right shape, for a developer's `.env` or a test."""
    return base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode()


class TotpSealer:
    """Seals and opens TOTP secrets. Built with no key, it refuses both with
    `Unavailable`: a process that was started without the key cannot enrol
    or check a second factor, and says so instead of storing a secret in the
    clear."""

    def __init__(self, key: str | None) -> None:
        self._aead = None if key is None else AESGCM(key_bytes(key))

    def _cipher(self) -> AESGCM:
        if self._aead is None:
            raise Unavailable("no TOTP encryption key is configured")
        return self._aead

    def seal(self, identity_id: UUID, secret: bytes) -> str:
        nonce = secrets.token_bytes(12)
        sealed = self._cipher().encrypt(nonce, secret, identity_id.bytes)
        return f"{VERSION}.{_b64(nonce)}.{_b64(sealed)}"

    def open(self, identity_id: UUID, sealed: str) -> bytes:
        """The secret, or `Unavailable` when this key did not seal it: a
        rotated key, or a secret moved between identities, is the process's
        problem, never the caller's."""
        cipher = self._cipher()
        try:
            version, nonce, body = sealed.split(".")
            if version != VERSION:
                raise ValueError(version)
            return cipher.decrypt(_unb64(nonce), _unb64(body), identity_id.bytes)
        except ValueError, InvalidTag:
            raise Unavailable("a TOTP secret does not open under this key") from None
