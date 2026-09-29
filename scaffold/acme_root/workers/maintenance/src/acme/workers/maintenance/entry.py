"""The console entry point: it installs the operating system's trust store
before the process imports anything, then runs the process.

truststore replaces ssl.SSLContext. A module that bound the old class first
(urllib3, and botocore through it) builds a context whose options setter
then calls itself until the process dies. So the injection comes before the
process's first import, here and not in main.py: a test that imports main.py
must not have ssl replaced halfway through its session."""

import sys


def main() -> int:
    from acme.infra.trust import install_trust_store

    install_trust_store()
    from acme.workers.maintenance.main import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
