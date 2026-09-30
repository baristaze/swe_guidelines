"""One named environment: where the API is, the two operator tokens it is
reached with, and where its signals are read back from. The operator's token
carries `read` and is what every read runs as; the provisioner's carries
`write` and is used only to create and remove the tenants a traffic run
needs. Neither is a sign-in: an agent never signs in to the operator plane,
it presents a token that expires within the hour (`acme-ops token` writes
one). `staging` and `production` are read from an owner-only file outside
the repository, `~/.config/acme/ops/<env>.env`, which every skill that
reads sources. The provisioner's token is never in it: it has a file of its
own beside it, `<env>.provisioner.env`, read only when a caller that drives
traffic asks for it, so a skill that reads holds no `write` token. An
environment file that holds the provisioner's token is refused, with the
line that moves it. `local` reads the same two files, which `make seed`
writes when they are absent: the compose stack's addresses and the tokens
of the two local operators it puts on the allowlist. Under the file,
`local` reads the compose stack's own knobs (`.env.example`, then `.env`)
from the repository root, so a key the file leaves out still names the
local stack. The process environment overrides either, key by key. A file
that anyone but its owner can read is refused."""

import os
import shlex
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

LOCAL_API_URL = "http://127.0.0.1:8000"
LOCAL_PROMETHEUS_URL = "http://localhost:59090"
LOCAL_JAEGER_URL = "http://localhost:56686"
LOCAL_ERROR_TRACKER_URL = "http://localhost:58000"
LOCAL_ERROR_TRACKER_TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
"""The read-only API token `deployment/local/glitchtip/seed.py` creates, fixed
the way the project key is, so the local reader needs no click through the UI."""

KEYS = (
    "ACME_API_URL",
    "ACME_OPERATOR_TOKEN",
    "ACME_ERROR_TRACKER_URL",
    "ACME_ERROR_TRACKER_TOKEN",
    "ACME_ERROR_TRACKER_ORG",
    # The product's project, not the environment's: the same value everywhere.
    "ACME_ERROR_TRACKER_PROJECT",
    "ACME_PROMETHEUS_URL",
    "ACME_JAEGER_URL",
    "ACME_AWS_PROFILE",
    "ACME_AWS_REGION",
    "SEED_SLUG",
    "SEED_EMAIL",
    "SEED_MEMBER_EMAIL",
)
"""Every key an environment file may set; anything else in it is ignored."""

PROVISIONER_KEY = "ACME_PROVISIONER_TOKEN"
"""The provisioner's `write` token: the one key of the provisioner's file, and
the one key the environment's file is refused for holding."""

PROVISIONER_SUFFIX = ".provisioner.env"

PROVISIONER_LINE = rf"^[[:space:]]*(export[[:space:]]+)?{PROVISIONER_KEY}[[:space:]]*="
"""A line of an environment file that sets the provisioner's token, as
`parse_env_file` reads one, for `grep -E`."""

CLOUD_ENVIRONMENTS = frozenset({"staging", "production"})

LOCAL_OPERATORS = {
    "operator": "operator@platform.acme.invalid",
    "provisioner": "provisioner@platform.acme.invalid",
}
"""The two operator identities `make seed` puts on the local allowlist: the
read operator, whose `read` token is the file's `ACME_OPERATOR_TOKEN`, and
the provisioner, whose `write` token is `ACME_PROVISIONER_TOKEN` in the
provisioner's file. They are the platform's own, made by their first grant,
so no person signs in as either and nothing but the grant command mints
their tokens."""

CLOUD_REGION = "us-west-2"
"""The region deployment/cloud/environments.json names; a test holds them equal."""


@dataclass(frozen=True)
class SeedPeople:
    """The org and the two people `make seed` creates, which a local run with
    `--orgs 0` drives instead of provisioning tenants of its own. They sign
    in by the local sign-in, by address alone."""

    slug: str
    owner_email: str
    member_email: str


@dataclass(frozen=True)
class Environment:
    name: str
    api_url: str
    operator_token: str | None
    provisioner_token: str | None
    error_tracker_url: str | None
    error_tracker_token: str | None
    error_tracker_org: str
    error_tracker_project: str
    """The product's one project in the tracker, the same in every
    environment: every process of every environment reports into it, and the
    environment is a property of each event, so a read names this project and
    filters on `name`. A project named per environment is the shape this is
    not."""
    prometheus_url: str | None
    jaeger_url: str | None
    aws_profile: str | None
    aws_region: str | None
    seed: SeedPeople | None

    @property
    def is_cloud(self) -> bool:
        return self.name in CLOUD_ENVIRONMENTS


def parse_env_file(text: str) -> dict[str, str]:
    """`KEY=value` lines; blank lines and `#` comments skipped, an `export `
    prefix and matching quotes dropped. The same shape `.env.example` has."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


def ops_file(name: str, home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "acme" / "ops" / f"{name}.env"


def provisioner_file_of(file: Path) -> Path:
    """The provisioner's file beside an environment's file: `staging.env`
    has `staging.provisioner.env`."""
    return file.with_name(f"{file.name.removesuffix('.env')}{PROVISIONER_SUFFIX}")


def provisioner_file(name: str, home: Path | None = None) -> Path:
    return provisioner_file_of(ops_file(name, home))


def keys_of(file: Path) -> tuple[str, ...]:
    """The keys a file may hold: the provisioner's file its one token, an
    environment's file every other key."""
    return (PROVISIONER_KEY,) if file.name.endswith(PROVISIONER_SUFFIX) else KEYS


def move_command(file: Path) -> str:
    """The shell line that moves the provisioner's token out of `file` into
    the provisioner's file beside it, both owner-only, printing neither."""
    line = shlex.quote(PROVISIONER_LINE)
    source = shlex.quote(str(file))
    target = shlex.quote(str(provisioner_file_of(file)))
    rest = shlex.quote(str(file.with_name(f"{file.name}.rest")))
    return (
        f"(umask 077 && grep -E {line} {source} > {target} && "
        f"{{ grep -vE {line} {source} > {rest} || true; }} && mv {rest} {source})"
    )


def refuse_a_provisioner_token(file: Path, values: Mapping[str, str], name: str) -> None:
    """Every skill that reads sources the environment's file, so the one
    `write` token is never in it: a file that holds it is refused
    before any value of it is used, with the line that moves it and the
    command that writes a fresh one instead."""
    if values.get(PROVISIONER_KEY):
        raise ValueError(
            f"{file} holds {PROVISIONER_KEY}, a write token, beside the read token every "
            f"skill that reads sources. It belongs in {provisioner_file_of(file)}, which only "
            f"traffic and stress read. Move the line, printing nothing:\n"
            f"  {move_command(file)}\n"
            f"or delete it and write a fresh one there: "
            f"uv run acme-ops token --env {name} --identity provisioner"
        )


def provisioner_token_in(file: Path, process_env: Mapping[str, str]) -> str | None:
    """The provisioner's token: its file's, refused when anyone but its owner
    can read it, with the process environment over it."""
    value: str | None = None
    if file.is_file():
        refuse_unless_owner_only(file)
        value = parse_env_file(file.read_text()).get(PROVISIONER_KEY) or None
    return process_env.get(PROVISIONER_KEY) or value


def refuse_unless_owner_only(file: Path) -> None:
    """The file holds operator tokens: one that its group or anyone else may
    read is refused, never read, whatever it holds right now."""
    mode = stat.S_IMODE(file.stat().st_mode)
    if mode & 0o077:
        raise ValueError(
            f"{file} is mode {mode:o}; it holds operator tokens and must be owner-only: "
            f"chmod 600 {file}"
        )


def repository_root(start: Path | None = None) -> Path | None:
    """The checkout `local` reads the compose knobs from: the nearest ancestor
    of the working directory with an `.env.example`, or git's answer."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".env.example").is_file():
            return candidate
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return None
    return Path(top) if top and (Path(top) / ".env.example").is_file() else None


def environment_of(
    name: str, values: Mapping[str, str], provisioner_token: str | None = None
) -> Environment:
    """An environment from resolved values. `local` fills what the file left
    out with the compose stack's addresses; a cloud environment leaves it out."""
    local = name not in CLOUD_ENVIRONMENTS

    def get(key: str, default: str | None = None) -> str | None:
        value = values.get(key)
        return value if value else default

    seed: SeedPeople | None = None
    if values.get("SEED_EMAIL") or local:
        seed = SeedPeople(
            slug=get("SEED_SLUG", "ajax") or "ajax",
            owner_email=get("SEED_EMAIL", "owner@example.test") or "owner@example.test",
            member_email=get("SEED_MEMBER_EMAIL", "bob@example.test") or "bob@example.test",
        )
    api_url = get("ACME_API_URL", LOCAL_API_URL if local else None)
    if not api_url:
        raise ValueError(f"environment {name!r} names no ACME_API_URL")
    return Environment(
        name=name,
        api_url=api_url.rstrip("/"),
        operator_token=get("ACME_OPERATOR_TOKEN"),
        provisioner_token=provisioner_token,
        error_tracker_url=get("ACME_ERROR_TRACKER_URL", LOCAL_ERROR_TRACKER_URL if local else None),
        error_tracker_token=get(
            "ACME_ERROR_TRACKER_TOKEN", LOCAL_ERROR_TRACKER_TOKEN if local else None
        ),
        error_tracker_org=get("ACME_ERROR_TRACKER_ORG", "acme") or "acme",
        error_tracker_project=get("ACME_ERROR_TRACKER_PROJECT", "acme") or "acme",
        prometheus_url=get("ACME_PROMETHEUS_URL", LOCAL_PROMETHEUS_URL if local else None),
        jaeger_url=get("ACME_JAEGER_URL", LOCAL_JAEGER_URL if local else None),
        aws_profile=get("ACME_AWS_PROFILE", None if local else f"acme-{name}-investigate"),
        aws_region=get("ACME_AWS_REGION", None if local else CLOUD_REGION),
        seed=seed,
    )


def load_environment(
    name: str,
    *,
    provisioner: bool = False,
    path: Path | None = None,
    home: Path | None = None,
    root: Path | None = None,
    process_env: Mapping[str, str] | None = None,
) -> Environment:
    """The file for `name` (or `path`), the repository's compose knobs under
    it for `local`, and the process environment over both. A cloud environment
    with no file is an error: its addresses and its operator are secrets that
    live nowhere else. The provisioner's token is read, from its own file and
    the process environment, only when `provisioner` asks for it; every other
    caller gets none."""
    if name != "local" and name not in CLOUD_ENVIRONMENTS:
        # A typo (`prod`) or an environment this tool has no reader for
        # (`dev`) would otherwise run against the local stack unannounced.
        known = ", ".join(sorted({"local", *CLOUD_ENVIRONMENTS}))
        raise ValueError(f"environment {name!r} is not one of {known}")
    values: dict[str, str] = {}
    local = name not in CLOUD_ENVIRONMENTS
    if local:
        checkout = root or repository_root()
        if checkout is not None:
            for candidate in (checkout / ".env.example", checkout / ".env"):
                if candidate.is_file():
                    values.update(parse_env_file(candidate.read_text()))
    file = path or ops_file(name, home)
    if file.is_file():
        refuse_unless_owner_only(file)
        found = parse_env_file(file.read_text())
        refuse_a_provisioner_token(file, found, name)
        values.update(found)
    elif not local:
        raise FileNotFoundError(f"no environment file for {name!r} at {file}")
    env = os.environ if process_env is None else process_env
    for key in KEYS:
        if env.get(key):
            values[key] = env[key]
    token = provisioner_token_in(provisioner_file_of(file), env) if provisioner else None
    return environment_of(name, {k: v for k, v in values.items() if k in KEYS}, token)


def local_addresses(env: Environment) -> dict[str, str]:
    """What a new `local.env` names beside its tokens: the compose stack's API,
    its error tracker, and the dashboards of the `devx` profile, as the
    repository's knobs resolve them, so a skill that sources the file reaches
    the local stack with nothing else to read."""
    named = {
        "ACME_API_URL": env.api_url,
        "ACME_ERROR_TRACKER_URL": env.error_tracker_url,
        "ACME_ERROR_TRACKER_TOKEN": env.error_tracker_token,
        "ACME_ERROR_TRACKER_ORG": env.error_tracker_org,
        "ACME_ERROR_TRACKER_PROJECT": env.error_tracker_project,
        "ACME_PROMETHEUS_URL": env.prometheus_url,
        "ACME_JAEGER_URL": env.jaeger_url,
    }
    return {key: value for key, value in named.items() if value}


def write_value(file: Path, key: str, value: str) -> None:
    """Sets `key` in the env file, replacing its line or appending one, and
    leaves every other line as it was. The file is created owner-only when
    missing, in a folder only its owner opens when that is missing too, and
    refused when it is not owner-only; the value is never echoed. The
    provisioner's token goes into the provisioner's file alone, and nothing
    else does."""
    if key not in keys_of(file):
        raise ValueError(f"{key} is not a key {file.name} holds")
    if file.is_file():
        refuse_unless_owner_only(file)
        lines = file.read_text().splitlines()
    else:
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lines = []
    line = f"{key}={value}"
    for index, raw in enumerate(lines):
        stripped = raw.strip().removeprefix("export ").lstrip()
        if stripped.partition("=")[0].strip() == key:
            lines[index] = line
            break
    else:
        lines.append(line)
    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as out:
        out.write("\n".join(lines) + "\n")
    os.chmod(file, 0o600)
