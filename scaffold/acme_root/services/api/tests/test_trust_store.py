"""The process installs the operating system's trust store before anything
binds ssl.SSLContext, so the AWS clients can open a TLS context, and a test
that imports the process's modules keeps the ssl module it started with.

The TLS cases run in a fresh interpreter: this test process has long since
imported urllib3, which is the order that breaks."""

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
ENTRY = PACKAGE / "src/acme/services/api" / "entry.py"


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


def test_the_console_script_starts_at_the_entry_module() -> None:
    scripts = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["acme-api"] == "acme.services.api.entry:main"


def test_the_entry_installs_the_trust_store_before_it_imports_the_process() -> None:
    tree = ast.parse(ENTRY.read_text())
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert [a.name for n in top_level for a in n.names] == ["sys"]
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "install_trust_store"
    ]
    process = [
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.ImportFrom) and n.module == "acme.services.api.main"
    ]
    assert calls and process and calls[0] < process[0]


def test_importing_the_process_leaves_ssl_alone() -> None:
    result = _run(
        "import ssl\n"
        "before = ssl.SSLContext\n"
        "import acme.services.api.main\n"
        "assert ssl.SSLContext is before\n"
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_the_entry_order_lets_botocore_open_a_tls_context() -> None:
    result = _run(
        "from acme.infra.trust import install_trust_store\n"
        "install_trust_store()  # what the entry does first\n"
        "import acme.services.api.main\n"
        "from botocore.httpsession import create_urllib3_context\n"
        "install_trust_store()  # what boot does, a no-op by now\n"
        "create_urllib3_context()\n"
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_the_order_the_entry_guards_against_does_break() -> None:
    # The negative control: bind the old class first, then inject. If this
    # stopped failing, the case above would prove nothing.
    result = _run(
        "from botocore.httpsession import create_urllib3_context\n"
        "from acme.infra.trust import install_trust_store\n"
        "install_trust_store()\n"
        "create_urllib3_context()\n"
    )
    assert result.returncode != 0
    assert "RecursionError" in result.stderr
