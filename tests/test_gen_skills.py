"""scripts/gen_skills.py: the review skills follow the template and the lens table."""

import pytest

from conftest import render_template


@pytest.fixture
def gen(repo):
    return repo.script("gen_skills")


def test_generated_skills_pass_check(repo, gen, capsys):
    assert gen.main(["--check"]) == 0
    assert "generated skills ok" in capsys.readouterr().out


def test_stale_skill_fails_check_and_is_left_alone(repo, gen, capsys):
    repo.edit("skills/_template/review.SKILL.md", "Never edit", "Never ever edit")
    before = repo.read("skills/arch-review-om/SKILL.md")
    assert gen.main(["--check"]) == 1
    assert "skills/arch-review-om/SKILL.md" in capsys.readouterr().out
    assert repo.read("skills/arch-review-om/SKILL.md") == before


def test_regenerate_writes_every_group_from_the_table(repo, gen, capsys):
    repo.edit("lenses/README.md", "principles  |\n", "principles  |\n| `ctx` | `ctx.md` | OpContext: tenancy |\n")
    repo.write("lenses/ctx.md", "# Context\n")
    assert gen.main([]) == 0
    assert "1 written, 1 unchanged" in capsys.readouterr().out
    assert repo.read("skills/arch-review-ctx/SKILL.md") == render_template("ctx", "Context", "OpContext: tenancy")


def test_lens_file_without_a_title_stops_generation(repo, gen):
    repo.write("lenses/om.md", "Group id: `om`.\n")
    with pytest.raises(SystemExit):
        gen.main(["--check"])


def test_unknown_argument_is_refused_and_writes_nothing(repo, gen):
    repo.edit("skills/_template/review.SKILL.md", "Never edit", "Never ever edit")
    before = repo.read("skills/arch-review-om/SKILL.md")
    with pytest.raises(SystemExit) as exit_:
        gen.main(["--chekc"])
    assert exit_.value.code == 2
    assert repo.read("skills/arch-review-om/SKILL.md") == before
