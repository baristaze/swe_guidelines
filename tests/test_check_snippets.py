"""scripts/check_snippets.py: every Python and YAML block in the Markdown parses."""

import pytest

CLEAN = """\
# Shapes

``` python
class Row(Base):
    org_id: UUID
    app: str

    async def get(self, ctx: TenantScope, row_id: UUID) -> Row: ...

rows = await manager.list(ctx)
```

1. A scenario, in a list item:

   ```yaml
   budget:
     max_usd: 3
   phases:
     - name: build
   ```

```ts
    const shiftedIsFine = "indentation is style here";
```
"""


@pytest.fixture
def snippets(repo):
    return repo.script("check_snippets")


def test_clean_blocks_pass(repo, snippets, capsys):
    repo.write("docs/shapes.md", CLEAN)
    assert snippets.main([]) == 0
    # the two here, and the one in the fixture's architecture.md
    assert "3 Python and YAML block(s) parse" in capsys.readouterr().out


def test_a_python_line_shifted_one_level_fails_at_its_line(repo, snippets, capsys):
    repo.write("docs/shapes.md", CLEAN.replace("    app: str", "        app: str"))
    assert snippets.main([]) == 1
    out = capsys.readouterr().out
    assert "docs/shapes.md:6: this python block does not parse: unexpected indent" in out


def test_a_yaml_key_shifted_under_a_scalar_fails(repo, snippets, capsys):
    repo.write("docs/shapes.md", CLEAN.replace("     max_usd: 3", "     max_usd: 3\n       cap: 4"))
    assert snippets.main([]) == 1
    assert "docs/shapes.md:" in capsys.readouterr().out


def test_a_block_is_found_in_every_markdown_file_the_scripts_read(repo, snippets, capsys):
    repo.write("scaffold/acme_root/.agents/skills/x/SKILL.md", "# x\n\n```python\nif True:\npass\n```\n")
    assert snippets.main([]) == 1
    assert "scaffold/acme_root/.agents/skills/x/SKILL.md:5: this python block does not parse" in capsys.readouterr().out


def test_an_unknown_argument_exits_2(snippets):
    with pytest.raises(SystemExit) as e:
        snippets.main(["--fix"])
    assert e.value.code == 2
