"""Every local Postgres reads healthy only once it takes a connection over TCP.

On an empty data directory the image's entrypoint runs the init scripts on a
server that listens on the local socket alone, then restarts it on the
network. A healthcheck over the socket reads healthy during that first
server, so a service that waits on `service_healthy` and connects over the
network is refused. A check that names a host, such as `pg_isready -h
127.0.0.1`, only the second server answers. Each compose file under
`deployment/local/` is read, every overlay merged over the base file.
"""

import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

LOCAL = Path(__file__).resolve().parents[2] / "deployment" / "local"
CLIENTS = {"pg_isready", "psql"}
SHELL_OPERATORS = {"&&", "||", ";", "|"}


class _Reset:
    """Compose's `!reset`: the key an earlier file set is gone."""


class _Override:
    """Compose's `!override`: the value replaces an earlier file's, unmerged."""

    def __init__(self, value: object) -> None:
        self.value = value


class _ComposeLoader(yaml.SafeLoader):
    """YAML with compose's two merge tags."""


def _construct_override(loader: yaml.SafeLoader, node: yaml.Node) -> _Override:
    if isinstance(node, yaml.MappingNode):
        return _Override(loader.construct_mapping(node, deep=True))
    if isinstance(node, yaml.SequenceNode):
        return _Override(loader.construct_sequence(node, deep=True))
    return _Override(loader.construct_object(node, deep=True))


_ComposeLoader.add_constructor("!reset", lambda loader, node: _Reset())
_ComposeLoader.add_constructor("!override", _construct_override)


def _services() -> dict[str, dict[str, Any]]:
    """Every service of the local stack, each overlay merged over the base file
    as compose merges them: a mapping key by key, any other value replaced."""
    files = [LOCAL / "docker-compose.yml", *sorted(LOCAL.glob("docker-compose.*.yml"))]
    merged: dict[str, dict[str, Any]] = {}
    for path in files:
        loaded: dict[str, Any] = yaml.load(path.read_text(), Loader=_ComposeLoader)
        for name, service in (loaded.get("services") or {}).items():
            into = merged.setdefault(name, {})
            for key, value in (service or {}).items():
                old = into.get(key)
                if isinstance(value, _Reset):
                    into.pop(key, None)
                elif isinstance(value, _Override):
                    into[key] = value.value
                elif isinstance(old, dict) and isinstance(value, dict):
                    into[key] = {**old, **value}
                else:
                    into[key] = value
    return merged


def _postgres_servers(services: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The services that run a Postgres server: the image, with a healthcheck or
    a service that waits on it being healthy. A one-shot on the image that runs
    `psql` against another instance is a client, and has neither."""
    awaited = {
        name
        for service in services.values()
        if isinstance(service.get("depends_on"), dict)
        for name, wait in service["depends_on"].items()
        if isinstance(wait, dict) and wait.get("condition") == "service_healthy"
    }
    return {
        name: service
        for name, service in services.items()
        if str(service.get("image", "")).startswith("postgres:")
        and ("healthcheck" in service or name in awaited)
    }


def _host(args: list[str]) -> str | None:
    """The host a client's arguments name, or None when they name none."""
    for i, arg in enumerate(args):
        if arg in {"-h", "--host"} and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--host="):
            return arg.removeprefix("--host=")
        if arg.startswith("-h") and len(arg) > 2:
            return arg[2:]
    return None


def asks_over_tcp(test: object) -> bool:
    """Whether a healthcheck's command runs a Postgres client, and every client
    it runs names a host that is an address: a socket directory starts with
    `/`, and a client with no host asks over the socket."""
    if isinstance(test, str):
        words = shlex.split(test)
    elif isinstance(test, list) and test and test[0] == "CMD-SHELL":
        words = shlex.split(" ".join(str(word) for word in test[1:]))
    elif isinstance(test, list) and test and test[0] == "CMD":
        words = [str(word) for word in test[1:]]
    else:
        return False
    hosts: list[str | None] = []
    for i, word in enumerate(words):
        if Path(word).name in CLIENTS:
            args: list[str] = []
            for arg in words[i + 1 :]:
                if arg in SHELL_OPERATORS:
                    break
                args.append(arg)
            hosts.append(_host(args))
    return bool(hosts) and all(host and not host.startswith("/") for host in hosts)


def test_every_local_postgres_is_healthy_only_over_tcp() -> None:
    servers = _postgres_servers(_services())
    assert servers, "no Postgres server found in deployment/local"
    over_the_socket = {
        name: service.get("healthcheck", {}).get("test")
        for name, service in servers.items()
        if not asks_over_tcp(service.get("healthcheck", {}).get("test"))
    }
    assert not over_the_socket, f"healthy before the network takes a connection: {over_the_socket}"


@pytest.mark.parametrize(
    "test",
    [
        ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U acme -d acme"],
        ["CMD", "pg_isready", "--host=localhost", "-U", "acme"],
        ["CMD-SHELL", "psql -h127.0.0.1 -U acme -d acme -c 'select 1' >/dev/null"],
        "pg_isready -h postgres-core -p 5432",
    ],
)
def test_a_check_that_names_a_host_asks_over_tcp(test: object) -> None:
    assert asks_over_tcp(test)


@pytest.mark.parametrize(
    "test",
    [
        ["CMD-SHELL", "pg_isready -U acme -d acme"],
        ["CMD", "pg_isready"],
        ["CMD-SHELL", "pg_isready -h /var/run/postgresql -U acme"],
        ["CMD-SHELL", "pg_isready -U acme && pg_isready -h 127.0.0.1"],
        ["CMD-SHELL", "true"],
        ["NONE"],
        None,
    ],
)
def test_a_check_over_the_socket_or_with_no_client_is_refused(test: object) -> None:
    assert not asks_over_tcp(test)
