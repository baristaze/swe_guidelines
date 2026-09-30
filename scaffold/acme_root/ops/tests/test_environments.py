"""An environment from its file, the compose knobs under it for local, and
the process environment over both. The provisioner's `write` token has a file
of its own, which only traffic and stress read."""

import json
import re
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from acme.ops import main as ops_main
from acme.ops.environments import (
    CLOUD_REGION,
    KEYS,
    LOCAL_API_URL,
    LOCAL_ERROR_TRACKER_TOKEN,
    LOCAL_OPERATORS,
    PROVISIONER_KEY,
    load_environment,
    move_command,
    ops_file,
    parse_env_file,
    provisioner_file,
    write_value,
)

REPOSITORY = Path(__file__).resolve().parents[2]


def owner_only(file: Path, text: str) -> Path:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
    file.chmod(0o600)
    return file


def test_env_lines_are_parsed_like_a_dotenv() -> None:
    values = parse_env_file(
        "# comment\nACME_API_URL=https://api.example.test/  # trailing\n"
        "export ACME_OPERATOR_TOKEN=\"opt_read\"\nSEED_SLUG='a c'\nBROKEN\n"
    )
    assert values == {
        "ACME_API_URL": "https://api.example.test/",
        "ACME_OPERATOR_TOKEN": "opt_read",
        "SEED_SLUG": "a c",
    }


def test_local_with_no_file_reads_the_checkout_and_the_fixed_addresses(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env.example").write_text("SEED_SLUG=ajax\nSEED_EMAIL=owner@example.test\n")
    (root / ".env").write_text("SEED_EMAIL=me@example.test\n")
    env = load_environment("local", home=tmp_path, root=root, process_env={})
    assert env.api_url == LOCAL_API_URL
    assert env.seed is not None and env.seed.owner_email == "me@example.test"
    assert env.seed.slug == "ajax"
    assert env.error_tracker_token == LOCAL_ERROR_TRACKER_TOKEN
    assert env.prometheus_url and env.jaeger_url and not env.is_cloud
    assert env.operator_token is None and env.provisioner_token is None


def test_a_cloud_environment_needs_its_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="staging"):
        load_environment("staging", home=tmp_path, root=tmp_path, process_env={})


def test_a_cloud_file_names_everything_and_the_process_env_overrides(tmp_path: Path) -> None:
    file = owner_only(
        tmp_path / ".config" / "acme" / "ops" / "staging.env",
        "ACME_API_URL=https://api.staging.example.test\n"
        "ACME_OPERATOR_TOKEN=opt_read\n"
        "ACME_ERROR_TRACKER_URL=https://sentry.io\nACME_ERROR_TRACKER_TOKEN=tok\n"
        "ACME_ERROR_TRACKER_ORG=ajax\n",
    )
    env = load_environment(
        "staging", home=tmp_path, root=tmp_path, process_env={"ACME_AWS_REGION": "eu-west-1"}
    )
    assert env.is_cloud and env.api_url == "https://api.staging.example.test"
    assert env.aws_profile == "acme-staging-investigate" and env.aws_region == "eu-west-1"
    assert env.error_tracker_org == "ajax" and env.seed is None
    assert env.operator_token == "opt_read" and env.provisioner_token is None
    assert file.stat().st_mode & 0o777 == 0o600
    assert env.prometheus_url is None and env.jaeger_url is None


def both_files(home: Path, name: str = "staging") -> tuple[Path, Path]:
    """The environment's file with the read token, and the provisioner's file
    beside it with the write token."""
    return (
        owner_only(
            ops_file(name, home),
            "ACME_API_URL=https://api.staging.example.test\nACME_OPERATOR_TOKEN=opt_read\n",
        ),
        owner_only(provisioner_file(name, home), "ACME_PROVISIONER_TOKEN=opt_write\n"),
    )


def test_a_read_loads_no_provisioner_token_and_traffic_reads_it_from_its_own_file(
    tmp_path: Path,
) -> None:
    """The file every read skill sources holds the read token alone. The
    provisioner's token is read from its own file, with the process
    environment over it, and only when a caller asks for it."""
    file, own = both_files(tmp_path)
    assert own == file.with_name("staging.provisioner.env")
    assert PROVISIONER_KEY not in KEYS
    process = {PROVISIONER_KEY: "opt_from_the_process"}
    for given in ({}, process):
        env = load_environment("staging", home=tmp_path, root=tmp_path, process_env=given)
        assert env.operator_token == "opt_read" and env.provisioner_token is None
    drives = load_environment("staging", provisioner=True, home=tmp_path, process_env={})
    assert drives.operator_token == "opt_read" and drives.provisioner_token == "opt_write"
    over = load_environment("staging", provisioner=True, home=tmp_path, process_env=process)
    assert over.provisioner_token == "opt_from_the_process"
    own.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only: chmod 600"):
        load_environment("staging", provisioner=True, home=tmp_path, process_env={})


@pytest.mark.parametrize(
    ("argv", "reads_it"),
    [
        (["traffic", "--env", "staging", "--profile", "light"], True),
        (["stress", "--scenario", "ops/stress/smoke.yaml", "--env", "staging"], True),
        (["signals", "check", "--env", "staging", "--request-id", "req_1"], False),
        (["size", "--env", "staging"], False),
        (["token", "--env", "staging", "--list"], False),
        (["token", "--env", "staging", "--identity", "operator"], False),
        (
            ["work", "requeue", "--env", "staging", "--org", str(UUID(int=1)), str(UUID(int=2))],
            False,
        ),
    ],
)
def test_only_traffic_and_stress_are_handed_the_provisioner_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], reads_it: bool
) -> None:
    """Each command's environment, as it is loaded, with both files in place:
    every command but the two that drive traffic gets no `write` token."""
    both_files(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in (*KEYS, PROVISIONER_KEY):
        monkeypatch.delenv(key, raising=False)
    loaded: list[str | None] = []
    real = ops_main.load_environment

    def stop_after_the_load(name: str, *, provisioner: bool = False) -> None:
        loaded.append(real(name, provisioner=provisioner).provisioner_token)
        raise ValueError("stopped after the load")

    monkeypatch.setattr(ops_main, "load_environment", stop_after_the_load)
    assert ops_main.main(argv) == 2
    assert loaded == ["opt_write" if reads_it else None]


OLD_FILE = (
    "ACME_API_URL=https://api.staging.example.test\n",
    "ACME_OPERATOR_TOKEN=opt_read\n",
    "# the tracker\n",
    "ACME_ERROR_TRACKER_ORG=acme\n",
)


@pytest.mark.parametrize(
    "line",
    [
        "ACME_PROVISIONER_TOKEN=opt_write\n",
        'export ACME_PROVISIONER_TOKEN="opt_write"\n',
        "  ACME_PROVISIONER_TOKEN = opt_write  # the generator's\n",
    ],
)
def test_an_env_file_that_holds_both_tokens_is_refused_with_the_line_that_moves_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    line: str,
) -> None:
    """A copy that upgrades keeps the file its operator wrote, with the write
    token in it. Every command refuses it, prints the line that moves the
    token and never the token, and that line, run as printed, leaves the read
    file with the read token alone and the provisioner's file with its own."""
    file = owner_only(ops_file("staging", tmp_path), "".join((*OLD_FILE[:2], line, *OLD_FILE[2:])))
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in (*KEYS, PROVISIONER_KEY):
        monkeypatch.delenv(key, raising=False)
    for argv in (["size", "--env", "staging"], ["traffic", "--env", "staging"]):
        assert ops_main.main(argv) == 2
        said = capsys.readouterr()
        assert move_command(file) in said.err
        assert "opt_write" not in said.out + said.err
    with pytest.raises(
        ValueError, match="uv run acme-ops token --env staging --identity provisioner"
    ):
        load_environment("staging", provisioner=True, home=tmp_path, process_env={})

    moved = subprocess.run(
        ["/bin/sh", "-c", move_command(file)], capture_output=True, text=True, check=False
    )
    assert moved.returncode == 0, moved.stderr
    assert moved.stdout + moved.stderr == ""
    own = provisioner_file("staging", tmp_path)
    assert file.read_text() == "".join(OLD_FILE)
    assert parse_env_file(own.read_text()) == {PROVISIONER_KEY: "opt_write"}
    assert file.stat().st_mode & 0o777 == 0o600 and own.stat().st_mode & 0o777 == 0o600
    env = load_environment("staging", home=tmp_path, process_env={})
    assert env.operator_token == "opt_read" and env.provisioner_token is None
    drives = load_environment("staging", provisioner=True, home=tmp_path, process_env={})
    assert drives.provisioner_token == "opt_write"


def test_an_empty_provisioner_line_holds_no_token_and_is_not_refused(tmp_path: Path) -> None:
    owner_only(
        ops_file("staging", tmp_path),
        "ACME_API_URL=https://api.staging.example.test\nACME_PROVISIONER_TOKEN=\n",
    )
    env = load_environment("staging", provisioner=True, home=tmp_path, process_env={})
    assert env.provisioner_token is None


def test_the_cloud_region_is_the_one_the_environments_name() -> None:
    layout = json.loads((REPOSITORY / "deployment" / "cloud" / "environments.json").read_text())
    assert CLOUD_REGION == layout["region"]


@pytest.mark.parametrize("name", ["prod", "dev", "Staging"])
def test_an_unknown_environment_is_refused_rather_than_run_locally(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(ValueError, match="is not one of local, production, staging"):
        load_environment(name, home=tmp_path, root=tmp_path, process_env={})


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_a_file_others_may_read_is_refused_before_it_is_read(tmp_path: Path, mode: int) -> None:
    file = owner_only(
        tmp_path / ".config" / "acme" / "ops" / "staging.env",
        "ACME_API_URL=https://api.staging.example.test\nACME_OPERATOR_TOKEN=opt_read\n",
    )
    file.chmod(mode)
    with pytest.raises(ValueError, match="owner-only: chmod 600"):
        load_environment("staging", home=tmp_path, root=tmp_path, process_env={})


def test_a_token_is_written_in_place_and_the_file_stays_owner_only(tmp_path: Path) -> None:
    lines = [
        "ACME_API_URL=https://api",
        "ACME_OPERATOR_TOKEN=",
        "# the tracker",
        "ACME_ERROR_TRACKER_URL=x",
    ]
    file = owner_only(tmp_path / "staging.env", "\n".join(lines) + "\n")
    write_value(file, "ACME_OPERATOR_TOKEN", "opt_new")
    write_value(file, "ACME_ERROR_TRACKER_TOKEN", "tok")
    assert file.read_text() == (
        "ACME_API_URL=https://api\nACME_OPERATOR_TOKEN=opt_new\n# the tracker\n"
        "ACME_ERROR_TRACKER_URL=x\nACME_ERROR_TRACKER_TOKEN=tok\n"
    )
    assert file.stat().st_mode & 0o777 == 0o600
    fresh = tmp_path / "new" / "production.provisioner.env"
    write_value(fresh, PROVISIONER_KEY, "opt_write")
    assert fresh.read_text() == "ACME_PROVISIONER_TOKEN=opt_write\n"
    assert fresh.stat().st_mode & 0o777 == 0o600
    assert fresh.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValueError, match="not a key"):
        write_value(file, "ACME_OPERATOR_PASSWORD", "secret")
    # The write token never goes into the file a read sources, and the
    # provisioner's file holds nothing else.
    with pytest.raises(ValueError, match=re.escape("not a key staging.env holds")):
        write_value(file, PROVISIONER_KEY, "opt_write")
    with pytest.raises(ValueError, match=re.escape("not a key production.provisioner.env holds")):
        write_value(fresh, "ACME_OPERATOR_TOKEN", "opt_read")


def test_the_seed_grants_the_local_operators_this_package_mints_for() -> None:
    """`make seed` puts the read operator and the provisioner on the local
    allowlist by address, and `acme-ops token --env local` mints for the same
    two: one list, read here from the recipe."""
    recipe = (REPOSITORY / "Makefile").read_text().split("\nseed:", 1)[1].split("\n\n", 1)[0]
    granted = re.findall(r"--permission (read|write) \\\n\s+--email (\S+)", recipe)
    assert granted == [
        ("read", LOCAL_OPERATORS["operator"]),
        ("write", LOCAL_OPERATORS["provisioner"]),
    ]
    assert "acme-ops token --env local --identity operator" in recipe
    assert "acme-ops token --env local --identity provisioner" in recipe
