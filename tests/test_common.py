"""scripts/_common.py: the heading and anchor rule every script shares."""

from _common import anchors, headings, slug


def test_slug_lowercases_drops_punctuation_and_hyphenates():
    assert slug("The Storage Layer") == "the-storage-layer"
    assert slug("Local: Docker Compose") == "local-docker-compose"
    assert slug("`OpContext` and *friends*") == "opcontext-and-friends"
    assert slug("  Park vs. fail  ") == "park-vs-fail"


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
