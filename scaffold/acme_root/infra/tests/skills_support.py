"""What the tests of the tree's skills share: where the skills are, and how
a test reads one.

A skill's own pins sit in the test file named for it,
`test_skill_<name>.py`, so two changes to two skills touch two files.
What every skill holds sits in `test_ops_skills.py`, and the signals the
ops skills read in `test_ops_signals.py`.

A count a skill states is pinned in its file too: every loop an agent
runs, with its count and what the skill does when the count is reached.
A strong model keeps going until something stops it, and a clock alone
stops nothing before the session runs out, so a rewrite that drops a
count fails there.
"""

import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".agents" / "skills"
SHARED = SKILLS / "_shared"
PREAMBLE = SHARED / "ops-preamble.md"
SIGNALS = SHARED / "signals"
REFERENCE = "../_shared/ops-preamble.md"
ENV_FILE = "~/.config/acme/ops/<env>.env"
PROVISIONER_FILE = "~/.config/acme/ops/<env>.provisioner.env"

# Every read of the operator plane a skill writes: a `curl` of a route under
# `/v1/admin/`, and the `jq` it is piped through on the next line. A read with
# no filter prints whatever the answer holds, and a read each run writes its
# own way reads something else on each run.
OPERATOR_READ = re.compile(
    r'^ *curl [^\n]*"\$ACME_API_URL/v1/admin/(?P<route>[^"]*)"'
    r"(?P<piped> \\\n +\| jq (?:-c )?'(?P<kept>[^'\n]*)'$)?",
    re.MULTILINE,
)

# A `SEARCH` schema names every dimension a series has, or it matches nothing
# and answers with no datapoint and no error: a read of nothing that looks
# like a quiet hour. The dashboard module writes the schemas the app's series
# carry, the exporter's own `OTelLib` among them, and its Terraform test holds
# them there. So a skill's schema is one of the dashboard's.
DASHBOARD = ROOT / "deployment" / "terraform" / "modules" / "dashboard"
SEARCH_SCHEMA = re.compile(r"SEARCH\(\W*?(\{[^}]*\})")


def own(pattern: str = "*") -> list[str]:
    """The tree's own skills, by folder name: a skill folder that is a link
    belongs to what it links to, such as a clone of the guideline's
    skills, and is held there."""
    found = SKILLS.glob(f"{pattern}/SKILL.md")
    return sorted(p.parent.name for p in found if not p.parent.is_symlink())


def text_of(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def prose_of(name: str) -> str:
    """The skill's text as one line: a rule is a sentence, wrapped where the
    margin falls, and the margin is not what these tests are about."""
    return " ".join(text_of(name).split())


def frontmatter(name: str) -> dict[str, object]:
    _, head, _ = text_of(name).split("---", 2)
    return yaml.safe_load(head)


def allowed_tools(name: str) -> str:
    return str(frontmatter(name).get("allowed-tools", ""))


def jq(program: str, answer: object) -> object:
    """What a read prints for an answer: one JSON value, never nothing."""
    done = subprocess.run(
        ["jq", "-c", program], input=json.dumps(answer), capture_output=True, text=True, check=True
    )
    (printed,) = done.stdout.splitlines()
    return json.loads(printed)


def search_schemas(text: str) -> set[str]:
    """Every `SEARCH` schema a text writes, as CloudWatch reads it: the quotes
    of the shell that carries the expression are skipped, and the backslashes
    of its JSON or its Terraform string are left out."""
    return {schema.replace("\\", "") for schema in SEARCH_SCHEMA.findall(text)}


def dashboard_schemas() -> set[str]:
    files = sorted(path for path in DASHBOARD.iterdir() if path.is_file())
    return set().union(*(search_schemas(path.read_text()) for path in files))
