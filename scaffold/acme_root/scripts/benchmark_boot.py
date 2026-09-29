"""What a process pays to boot: importing the roots, then building the
storage root, the infra root, and every manager (ADR 0007). `make benchmark-boot`, or
`uv run --package acme-api python scripts/benchmark_boot.py`."""

import sys
import tempfile
import time
from pathlib import Path


def ms(start: float) -> str:
    return f"{(time.perf_counter() - start) * 1000:8.2f} ms"


def main() -> int:
    rows: list[tuple[str, str]] = []
    before = len(sys.modules)

    t = time.perf_counter()
    import acme.om.root

    rows.append(("import acme.om.root (every manager and storage interface)", ms(t)))
    t = time.perf_counter()
    import acme.infra.impl.configured

    rows.append(("import acme.infra.impl.configured", ms(t)))
    t = time.perf_counter()
    import acme.services.api.app  # noqa: F401

    rows.append(("import acme.services.api.app (FastAPI, routers, container)", ms(t)))
    rows.append(("modules loaded by those imports", f"{len(sys.modules) - before:8d}"))

    from acme.infra.impl.local import InfraLocalImpl
    from acme.om.root import build_managers
    from acme.om.storage.impl.memory import StorageMemoryImpl
    from acme.om.storage.impl.postgres import StoragePostgresImpl
    from acme.om.storage.roles import DatabaseRole
    from acme.om.storage.settings import StorageSettings

    # The urls and the bounds come from settings, the way a composition root
    # hands them to the storage root; reading them is not what is timed.
    urls = dict.fromkeys(DatabaseRole, "postgresql+asyncpg://t:t@127.0.0.1:1/x")
    system_urls = dict.fromkeys(DatabaseRole, "postgresql+asyncpg://s:s@127.0.0.1:1/x")
    pools = StorageSettings().role_pools()

    tmp = Path(tempfile.mkdtemp())
    t = time.perf_counter()
    memory = StorageMemoryImpl()
    rows.append(("StorageMemoryImpl()", ms(t)))
    t = time.perf_counter()
    infra = InfraLocalImpl(tmp)
    rows.append(("InfraLocalImpl()", ms(t)))
    t = time.perf_counter()
    build_managers(memory, infra)
    rows.append(("build_managers() over memory", ms(t)))
    t = time.perf_counter()
    postgres = StoragePostgresImpl(urls, pools, system_urls=system_urls)
    rows.append(("StoragePostgresImpl() (engines built, nothing connects)", ms(t)))
    t = time.perf_counter()
    build_managers(postgres, infra)
    rows.append(("build_managers() over postgres", ms(t)))

    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name:<{width}}  {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
