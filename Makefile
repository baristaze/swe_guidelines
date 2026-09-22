# Software Design and Architecture Guidelines: checks and generators
SHELL := /bin/bash
# The scripts run through uv, so they run on the Python uv selects: the
# one UV_PYTHON names (CI sets it per leg), else the one on PATH.
PYTHON := uv run --no-project python
# The tests need pytest, pyyaml, and jsonschema, which a system python3 may
# not carry; uv brings them at pinned versions, locally and in CI alike.
PYTEST := uv run --no-project --with pytest==9.1.1 --with pyyaml==6.0.3 --with jsonschema==4.26.0 python -m pytest
NPX := npx --yes
MARKDOWNLINT := $(NPX) markdownlint-cli2@0.23.2
# ruff and mypy run at pinned versions through uvx; pyproject.toml holds their configuration
RUFF := uvx ruff@0.16.8
MYPY := uvx --with pytest==9.1.1 mypy@2.3.1

.PHONY: help check lint ruff mypy lenses leaks links toc version skills agents test plugin gen-skills gen-skills-check gen-toc benchmark benchmark-serve clean

help:              ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

check: lint ruff mypy lenses leaks links toc version gen-skills-check skills agents test plugin ## run every check (what CI runs)

lint:              ## markdownlint over every Markdown file
	$(MARKDOWNLINT) "**/*.md" "#node_modules"

ruff:              ## lint and format check of scripts/, benchmark/, checkers/, and tests/
	$(RUFF) check
	$(RUFF) format --check

mypy:              ## type check of scripts/, benchmark/, checkers/src/, and tests/
	$(MYPY)

lenses:            ## every lens follows the format and cites a real section
	$(PYTHON) scripts/check_lenses.py

leaks:             ## no product or hardware vocabulary in the Markdown
	$(PYTHON) scripts/check_leaks.py

links:             ## every relative link and anchor resolves
	$(PYTHON) scripts/check_links.py

toc:               ## the table of contents of architecture.md matches its headings
	$(PYTHON) scripts/gen_toc.py --check

version:           ## every copy of the release version agrees with .claude-plugin/plugin.json
	$(PYTHON) scripts/check_version.py

skills:            ## every skill has valid frontmatter and references files that exist
	$(PYTHON) scripts/check_skills.py

agents:            ## the reviewer agent mirrors the review template (decision words, report block, step count)
	$(PYTHON) scripts/check_agents.py

test:              ## the checkers and generators pass their own tests (pytest through uv, pinned)
	$(PYTEST) tests -q

plugin:            ## validate the plugin, marketplace, skills, and agents with Claude Code (skipped when claude is not installed)
	@if command -v claude >/dev/null 2>&1; then \
	  claude plugin validate . --strict && claude plugin validate skills --strict && claude plugin validate agents --strict \
	  && $(PYTHON) scripts/check_plugin.py; \
	else echo "plugin: claude not installed, skipped"; fi

gen-skills:        ## regenerate the review skills from the template and the lens files
	$(PYTHON) scripts/gen_skills.py

gen-skills-check:  ## fail when a generated skill is out of date
	$(PYTHON) scripts/gen_skills.py --check

gen-toc:           ## regenerate the table of contents of architecture.md
	$(PYTHON) scripts/gen_toc.py

benchmark:         ## run the smoke benchmark scenario (calls paid APIs; not part of check)
	uv run benchmark/run.py --scenario explain-tenancy --providers 3 --effort medium --repeat 1

benchmark-serve:   ## serve the benchmark runs folder at http://127.0.0.1:8765/
	uv run benchmark/serve.py --runs benchmark/runs --port 8765

clean:             ## remove tool caches
	rm -rf .markdownlint-cli2-cache node_modules .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './node_modules/*' -prune -exec rm -rf {} +
