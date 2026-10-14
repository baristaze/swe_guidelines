"""`ops-simulate-traffic` drives traffic through `acme-ops`, which reads
the provisioner's token itself, and waits for the generator's own
refusal when the environment holds none."""

from skills_support import PREAMBLE, ROOT, prose_of

NAME = "ops-simulate-traffic"


def test_the_refusal_a_traffic_skill_waits_for_is_the_generators() -> None:
    said = "holds no provisioner token"
    assert said in prose_of(NAME)
    assert said in " ".join(PREAMBLE.read_text().split())
    assert said in (ROOT / "ops" / "src" / "acme" / "ops" / "traffic.py").read_text()
