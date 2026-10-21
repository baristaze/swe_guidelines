"""`.env` and `.env.example` are defaults everywhere they are read: a variable
exported in the shell wins over both, as it does for the settings.

The Makefile includes the two files, and Make lets an included file beat the
environment, so it keeps each exported name aside and puts it back. That is
what makes `ACME_DATABASE_MIGRATION_URL=<other> make migrate` migrate
`<other>` and not the database `.env` names. `scripts/dev.sh` reads `.env`
itself and skips a name the shell already exports. Each runs here in a copy
of the repository's files, with a probe in place of the real commands.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
URL = "ACME_DATABASE_MIGRATION_URL"
IN_DOTENV = "postgresql+asyncpg://acme_migration:acme_migration@127.0.0.1:55432/from_dotenv"
# A password with the characters Make treats as syntax: a comment and a reference.
EXPORTED = "postgresql+asyncpg://acme_migration:p#a$$b$(x)@127.0.0.1:55432/exported"

# The recipe's environment, then the value Make itself holds, of the URL; then
# a knob Make reads for a recipe's arguments.
PROBE = (
    "probe:\n"
    "\t@printf '%s\\n' \"$${ACME_DATABASE_MIGRATION_URL-unset}\""
    " '$(value ACME_DATABASE_MIGRATION_URL)' '$(SEED_EMAIL)'\n"
)


def _shell(**exported: str) -> dict[str, str]:
    """The test process's environment without any knob of its own, and
    without the Make state a nested make would inherit from `make check`."""
    inherited = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ACME_", "SEED_", "VITE_"))
        and name not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}
    }
    return {**inherited, **exported}


def _checkout(tmp_path: Path) -> Path:
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    (tmp_path / ".env").write_text(f"{URL}={IN_DOTENV}\nSEED_EMAIL=dotenv@example.test\n")
    (tmp_path / "probe.mk").write_text(PROBE)
    return tmp_path


def _make(checkout: Path, env: dict[str, str], *arguments: str) -> list[str]:
    done = subprocess.run(
        ["make", "-s", "-f", "Makefile", "-f", "probe.mk", "probe", *arguments],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.splitlines()


def test_make_takes_the_exported_value_over_both_files(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    shell = _shell(**{URL: EXPORTED, "SEED_EMAIL": "shell@example.test"})
    assert _make(checkout, shell) == [EXPORTED, EXPORTED, "shell@example.test"]


def test_make_takes_dotenv_over_the_example_when_nothing_is_exported(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    # Not exported, so the recipe's process reads `.env` for itself.
    assert _make(checkout, _shell()) == ["unset", IN_DOTENV, "dotenv@example.test"]


def test_the_make_command_line_still_wins_over_the_shell(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    shell = _shell(SEED_EMAIL="shell@example.test")
    assert _make(checkout, shell, "SEED_EMAIL=line@example.test")[2] == "line@example.test"


def test_dev_script_takes_the_exported_value_over_dotenv(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "dev.sh", tmp_path / "scripts" / "dev.sh")
    (tmp_path / ".env").write_text(f"{URL}={IN_DOTENV}\nSEED_EMAIL=dotenv@example.test\n")
    seen = tmp_path / "seen"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Every process the script starts records the two values it was given.
    for tool in ("uv", "pnpm"):
        fake = bin_dir / tool
        fake.write_text(f'#!/bin/sh\necho "${URL}|$SEED_EMAIL" >> "{seen}"\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    shell = _shell(**{URL: EXPORTED, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
    subprocess.run(["bash", "scripts/dev.sh"], cwd=tmp_path, env=shell, check=True, timeout=30)
    # However many processes the script starts, each saw the same two values.
    lines = seen.read_text().splitlines()
    assert lines
    assert set(lines) == {f"{EXPORTED}|dotenv@example.test"}


SEEDED = (
    "seeded:\n"
    "\t@printf '%s\\n' '$(SEED_OWNER_EMAIL)' '$(SEED_ADMIN_EMAIL)' '$(SEED_MEMBER_EMAIL)'\n"
)


def _seeded(checkout: Path, env: dict[str, str], *arguments: str) -> list[str]:
    (checkout / "seeded.mk").write_text(SEEDED)
    done = subprocess.run(
        ["make", "-s", "-f", "Makefile", "-f", "seeded.mk", "seeded", *arguments],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.splitlines()


def test_each_seeded_person_is_a_plus_address_of_the_base(tmp_path: Path) -> None:
    """Every seeded person has an address WorkOS can send a code to, all in
    the mailbox of the one base address: the owner of the first org, the
    owner of the second (an admin of the first), and the member."""
    checkout = _checkout(tmp_path)
    assert _seeded(checkout, _shell(), "SEED_EMAIL=dev@example.com") == [
        "dev+acme+local+ajax@example.com",
        "dev+acme+local+fabrikam@example.com",
        "dev+acme+local+personal@example.com",
    ]


def test_with_no_base_the_seed_takes_gits_address(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    (checkout / ".env").write_text("")
    git_config = tmp_path / "gitconfig"
    git_config.write_text("[user]\n\temail = me@example.org\n")
    shell = _shell(GIT_CONFIG_GLOBAL=str(git_config), GIT_CONFIG_NOSYSTEM="1")
    assert _seeded(checkout, shell)[0] == "me+acme+local+ajax@example.org"


def _seed(checkout: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """`make seed` in the copy, with a `uv` that records the arguments of each
    command it is handed in place of running it, and a home of its own."""
    bin_dir = checkout / "bin"
    bin_dir.mkdir(exist_ok=True)
    ran = checkout / "ran"
    fake = bin_dir / "uv"
    fake.write_text(f'#!/bin/sh\necho "$*" >> "{ran}"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    path = f"{bin_dir}{os.pathsep}{env.get('PATH', os.defpath)}"
    done = subprocess.run(
        ["make", "-s", "-f", "Makefile", "seed"],
        cwd=checkout,
        env={**env, "PATH": path, "HOME": str(checkout)},
        capture_output=True,
        text=True,
        check=False,
    )
    return done, ran.read_text().splitlines() if ran.exists() else []


@pytest.mark.parametrize(
    "base", ["owner@example.test", "me@acme.example", "me@acme.invalid", "me@localhost"]
)
def test_the_seed_refuses_a_base_on_a_domain_that_takes_no_mail(tmp_path: Path, base: str) -> None:
    """A checkout whose `.env` still names an address on a reserved domain
    would seed people WorkOS can send no code to. The seed refuses it before
    it writes anything, naming the line to change, and `make reset` for a
    database seeded before, whose orgs the seed would keep."""
    checkout = _checkout(tmp_path)
    (checkout / ".env").write_text(f"SEED_EMAIL={base}\n")
    done, ran = _seed(checkout, _shell())
    assert done.returncode == 2, done.stderr
    assert f"make seed: {base} is on a domain that takes no mail" in done.stderr
    assert "the SEED_EMAIL= line in .env" in done.stderr
    assert "`make reset`" in done.stderr
    assert ran == []


def test_the_seed_takes_a_base_that_takes_mail(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    (checkout / ".env").write_text("SEED_EMAIL=dev@example.com\n")
    done, ran = _seed(checkout, _shell())
    assert done.returncode == 0, done.stderr
    assert "--email dev+acme+local+ajax@example.com" in ran[0]

