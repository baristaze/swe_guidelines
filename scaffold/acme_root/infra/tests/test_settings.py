"""Every knob is documented: each field of `InfraSettings` appears in
`.env.example` under its prefix, so a knob added to the settings and not
written down fails the fast gate. The file is read as text, the way a
developer reads it; a commented-out line documents a knob too."""

import re
import subprocess
from pathlib import Path

from acme.infra.impl.settings import SECRET_ENV_PREFIX, InfraSettings

PREFIX = InfraSettings.model_config.get("env_prefix", "")

NOT_A_KNOB = {
    "secret_overrides": f"the {SECRET_ENV_PREFIX}<ORG>_<NAME> family, shown by its example line",
}


def repository_root() -> Path:
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent,
    ).stdout.strip()
    return Path(top)


def documented_knobs(env_example: str) -> set[str]:
    """The names on `NAME=` and `#NAME=` lines."""
    return set(re.findall(r"^#?([A-Z][A-Z0-9_]*)=", env_example, flags=re.MULTILINE))


def test_every_infra_setting_is_in_env_example() -> None:
    documented = documented_knobs((repository_root() / ".env.example").read_text())
    # The override family is a tenant's: its example names the org in hex.
    assert any(
        re.fullmatch(rf"{SECRET_ENV_PREFIX}[0-9A-F]{{32}}_EXAMPLE_TOKEN", knob)
        for knob in documented
    )
    missing = sorted(
        f"{PREFIX}{field.upper()}"
        for field in InfraSettings.model_fields
        if field not in NOT_A_KNOB and f"{PREFIX}{field.upper()}" not in documented
    )
    assert not missing, f"settings fields absent from .env.example: {missing}"
