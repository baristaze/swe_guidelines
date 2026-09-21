"""scripts/_common.py: the heading and anchor rule every script shares."""

import pytest

from _common import anchors, headings, markdown_files, slug

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


# Each heading beside the anchor github-slugger 2.0.0, the package GitHub's
# anchors follow, gives for the heading's rendered text. The anchors come
# from running the package, not from reading it:
#
#     npm install github-slugger@2.0.0
#     # run.mjs
#     import { slug } from "github-slugger";
#     console.log(slug(process.argv[2]));
#
# `node run.mjs "<text>"` takes the rendered text: the link syntax, the
# backticks, the emphasis markers, and a closing `#` sequence already gone.
GITHUB_SLUGGER = [
    # each space is a hyphen, so a run of spaces is a run of hyphens
    ("Park  vs fail", "park--vs-fail"),
    ("Two   spaces and a tab\there", "two---spaces-and-a-tabhere"),
    ("`code` - dash", "code---dash"),
    # a link keeps its text
    ("See [the lenses](lenses/README.md#groups)", "see-the-lenses"),
    ("[Cache](#cache) and friends", "cache-and-friends"),
    # a closing sequence of `#` is not part of the heading
    ("Tables ##", "tables"),
    ("Use C #", "use-c"),
    ("C++ and C#", "c-and-c"),
    # underscores stay
    ("CTX-13 The system scope is EMPTY_UUID", "ctx-13-the-system-scope-is-empty_uuid"),
    ("`snake_case` heading", "snake_case-heading"),
    # inline code and emphasis keep their text
    ("`OpContext` and *friends*", "opcontext-and-friends"),
    # punctuation drops out
    ("What's new?", "whats-new"),
    ("Tables (and more)", "tables-and-more"),
    ("Local: Docker Compose", "local-docker-compose"),
    ("Park vs. fail", "park-vs-fail"),
    ("A/B, C & D!", "ab-c--d"),
    ("100% done", "100-done"),
    # letters outside ASCII stay, lowercased
    ("Émigré café", "émigré-café"),
    ("Straße und Größe", "straße-und-größe"),
    ("日本語 見出し", "日本語-見出し"),
    ("naïve — em dash", "naïve--em-dash"),
]


@pytest.mark.parametrize(("heading", "anchor"), GITHUB_SLUGGER)
def test_anchor_matches_github_slugger(heading, anchor):
    [(_level, _title, found)] = anchors(f"## {heading}\n")
    assert found == anchor


def test_closing_hashes_are_not_part_of_the_heading():
    text = "## Tables ##\n\n### Use C# #\n\n## C#\n"
    assert headings(text) == [(2, "Tables"), (3, "Use C#"), (2, "C#")]
    assert [a for _, _, a in anchors(text)] == ["tables", "use-c", "c"]


def test_markdown_files_reach_every_depth_and_skip_caches_and_runs(repo):
    for rel in [
        "docs/sub/deep/x.md",
        "node_modules/pkg/README.md",
        "docs/node_modules/pkg/README.md",
        ".pytest_cache/README.md",
        ".venv/lib/README.md",
        ".git/x.md",
        ".claude/worktrees/agent-1/architecture.md",
        "benchmark/runs/one/report.md",
        "benchmark/README.md",
        "docs/notes.txt",
    ]:
        repo.write(rel, "# X\n")
    found = {p.relative_to(repo.root).as_posix() for p in markdown_files(repo.root)}
    assert "docs/sub/deep/x.md" in found
    assert "benchmark/README.md" in found
    assert not {f for f in found if f.split("/")[0] in {"node_modules", ".pytest_cache", ".venv", ".git", ".claude"}}
    assert "docs/node_modules/pkg/README.md" not in found
    assert "benchmark/runs/one/report.md" not in found
    assert "docs/notes.txt" not in found


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
