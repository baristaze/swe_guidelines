"""Where the CLI finds the API and the credential. The environment wins
(`ACME_API_URL`, `ACME_TOKEN`), then the session file `acme login`
writes under `ACME_HOME` (default `~/.config/acme`), then the local
default. `ACME_TOKEN` may hold an api key as well as a session token; the
API accepts either as a bearer. `ACME_HTTP_TIMEOUT_SECONDS` bounds every
call the client makes, `ACME_HTTP_RETRIES` how many extra attempts a
retryable failure gets, and `ACME_HTTP_RETRY_BACKOFF_SECONDS` the wait
before the first of them."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 0.25
SESSION_FILE = "session.json"


class BadSetting(ValueError):
    """A setting the environment names cannot be used. That is the caller's
    input, not a failure of the run, so the CLI tells it in one line and
    exits 2, the way it tells any other usage error."""


@dataclass(frozen=True)
class Session:
    api_url: str
    token: str
    email: str
    display_name: str
    org_slug: str
    org_name: str


def home() -> Path:
    override = os.environ.get("ACME_HOME")
    return Path(override) if override else Path.home() / ".config" / "acme"


def load_session() -> Session | None:
    path = home() / SESSION_FILE
    try:
        return Session(**json.loads(path.read_text()))
    except OSError, ValueError, TypeError:
        return None


def save_session(session: Session) -> Path:
    """The token is a secret: the file is created owner-only, not created
    readable and locked down after, and so is the directory that holds it.
    A place that will not take the file is ACME_HOME's doing, so it is told
    as a setting and not as a traceback."""
    path = home() / SESSION_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(json.dumps(asdict(session), indent=2) + "\n")
        path.chmod(0o600)  # a file an earlier version left readable
    except OSError as error:
        raise BadSetting(f"the session cannot be kept in {path.parent}: {error}") from None
    return path


def clear_session() -> bool:
    path = home() / SESSION_FILE
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as error:
        raise BadSetting(f"the session cannot be forgotten: {error}") from None
    return True


def api_url(option: str | None = None) -> str:
    """`--api`, then the environment, then the session's, then local."""
    if option:
        return option
    if from_env := os.environ.get("ACME_API_URL"):
        return from_env
    session = load_session()
    return session.api_url if session else DEFAULT_API_URL


def timeout_seconds() -> float:
    """The timeout every call carries: the environment, else the default."""
    raw = os.environ.get("ACME_HTTP_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        raise BadSetting(f"ACME_HTTP_TIMEOUT_SECONDS is not a number: {raw!r}") from None
    if seconds <= 0:
        raise BadSetting(f"ACME_HTTP_TIMEOUT_SECONDS must be positive: {raw!r}")
    return seconds


def retries() -> int:
    """How many extra attempts a retryable failure gets: the environment, else
    the default. Zero sends every call exactly once."""
    raw = os.environ.get("ACME_HTTP_RETRIES")
    if not raw:
        return DEFAULT_RETRIES
    try:
        count = int(raw)
    except ValueError:
        raise BadSetting(f"ACME_HTTP_RETRIES is not a whole number: {raw!r}") from None
    if count < 0:
        raise BadSetting(f"ACME_HTTP_RETRIES cannot be negative: {raw!r}")
    return count


def backoff_seconds() -> float:
    """The wait before the first extra attempt; it doubles from there and
    carries jitter. The environment, else the default."""
    raw = os.environ.get("ACME_HTTP_RETRY_BACKOFF_SECONDS")
    if not raw:
        return DEFAULT_BACKOFF_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        raise BadSetting(f"ACME_HTTP_RETRY_BACKOFF_SECONDS is not a number: {raw!r}") from None
    if seconds <= 0:
        raise BadSetting(f"ACME_HTTP_RETRY_BACKOFF_SECONDS must be positive: {raw!r}")
    return seconds


def token() -> str | None:
    """The bearer every request carries. It travels in a header, which carries
    ascii and nothing else, so a value that does not is refused here rather
    than encoded at the wire; the value is a secret, so the line names the
    variable and never what is in it."""
    if from_env := os.environ.get("ACME_TOKEN"):
        if not from_env.isascii():
            raise BadSetting("ACME_TOKEN has characters a request header cannot carry")
        return from_env
    session = load_session()
    return session.token if session else None


def credentials(option: str | None = None) -> tuple[str, str | None]:
    """The API and the bearer a command sends, as a pair: a token goes only
    to the API it belongs to. `ACME_TOKEN` goes to `--api`, else
    `ACME_API_URL`, else local, never to the API the session file names.
    The session file's token goes to the API that issued it; an `--api` or a
    `ACME_API_URL` naming another one is refused, not handed that token."""
    explicit = option or os.environ.get("ACME_API_URL") or None
    if os.environ.get("ACME_TOKEN"):
        return explicit or DEFAULT_API_URL, token()
    session = load_session()
    if session is None:
        return explicit or DEFAULT_API_URL, None
    if explicit is not None and explicit.rstrip("/") != session.api_url.rstrip("/"):
        raise BadSetting(
            f"the session was issued by {session.api_url}, not {explicit}; "
            f"run `acme login --api {explicit}` there, or set ACME_TOKEN"
        )
    return session.api_url, session.token
