"""Pure rules of the slack namespace: the install state, its digest, and the
name the bot token is kept under. Values in, values out."""

import hashlib
import secrets
from uuid import UUID


def new_state() -> str:
    """A fresh install state: 256 bits, URL-safe, since it rides in Slack's
    redirect back to the platform."""
    return secrets.token_urlsafe(32)


def state_digest(state: str) -> str:
    """The SHA-256 digest of a state: the only form stored."""
    return hashlib.sha256(state.encode()).hexdigest()


def credential_ref_for(installation_id: UUID) -> str:
    """The name the installation's tokens are kept under in the org's own
    secrets: one segment, and one name per installation, so a reinstall never
    reads a token an uninstall is deleting."""
    return f"slack_bot_{installation_id.hex}"
