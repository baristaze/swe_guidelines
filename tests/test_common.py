"""scripts/_common.py: the heading and anchor rule every script shares."""

import pytest
from _common import anchors, headings, slug

SCRIPTS = [
    "check_agents",
    "check_leaks",
    "check_lenses",
    "check_links",
    "check_skills",
    "check_version",
    "gen_skills",
    "gen_toc",
]


@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_refuses_an_unknown_argument(repo, name):
    with pytest.raises(SystemExit) as exit_:
        repo.script(name).main(["--chekc"])
    assert exit_.value.code == 2


def test_slug_lowercases_drops_punctuation_and_hyphenates():
    assert slug("The Storage Layer") == "the-storage-layer"
    assert slug("Local: Docker Compose") == "local-docker-compose"
    assert slug("`OpContext` and *friends*") == "opcontext-and-friends"
    assert slug("  Park vs. fail  ") == "park-vs-fail"


def test_slug_keeps_underscores_the_way_github_does():
    assert slug("CTX-13 The system scope is EMPTY_UUID") == "ctx-13-the-system-scope-is-empty_uuid"
    assert slug("`snake_case` heading") == "snake_case-heading"


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        # each pair is the anchor GitHub renders for the heading
        ("Park  vs fail", "park--vs-fail"),
        ("`code` - dash", "code---dash"),
        ("See [the lenses](lenses/README.md#groups)", "see-the-lenses"),
        ("[Cache](#cache) and friends", "cache-and-friends"),
        ("What's new?", "whats-new"),
        ("Tables (and more)", "tables-and-more"),
        ("EMPTY_UUID and snake_case", "empty_uuid-and-snake_case"),
    ],
)
def test_slug_matches_github_anchors(heading, anchor):
    assert slug(heading) == anchor


def test_closing_hashes_are_not_part_of_the_heading():
    text = "## Tables ##\n\n### Use C# #\n\n## C#\n"
    assert headings(text) == [(2, "Tables"), (3, "Use C#"), (2, "C#")]
    assert [a for _, _, a in anchors(text)] == ["tables", "use-c", "c"]


def test_headings_skip_fenced_code_and_keep_levels():
    text = "# One\n\n```\n# not a heading\n```\n\n## Two\n\n### Three  \n"
    assert headings(text) == [(1, "One"), (2, "Two"), (3, "Three")]


def test_anchors_number_repeats_the_way_github_does():
    text = "## Principles\n\n## Tables\n\n## Principles\n\n## Principles\n"
    assert [a for _, _, a in anchors(text)] == [
        "principles",
        "tables",
        "principles-1",
        "principles-2",
    ]
