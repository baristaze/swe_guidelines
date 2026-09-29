"""The migration check compares against every table even in a fresh process,
where nothing else has imported the table modules. Inside the test session
other tests have already imported them, so the probe runs in a subprocess."""

import json
import subprocess
import sys

from acme.om.storage.roles import TABLE_ROLES, DatabaseRole

PROBE = """
import json
from acme.om.storage.migrate import role_metadata
from acme.om.storage.roles import DatabaseRole
tables = {r.value: sorted(t.name for t in role_metadata(r).tables.values()) for r in DatabaseRole}
print(json.dumps(tables))
"""


def test_role_metadata_holds_every_table_in_a_fresh_process() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, check=True
    )
    expected = {
        role.value: sorted(name for name, owner in TABLE_ROLES.items() if owner is role)
        for role in DatabaseRole
    }
    assert json.loads(result.stdout) == expected
