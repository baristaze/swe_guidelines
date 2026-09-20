# Software Design and Architecture Guidelines: checks and generators
SHELL := /bin/bash
PYTHON := python3
NPX := npx --yes
MARKDOWNLINT := $(NPX) markdownlint-cli2@0.23.2

.PHONY: help check lint lenses leaks links prose toc version skills agents test plugin gen-skills gen-skills-check gen-toc clean

help:              ## show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

check: lint lenses leaks links prose toc version gen-skills-check skills agents test plugin ## run every check (what CI runs)

lint:              ## markdownlint over every Markdown file
	$(MARKDOWNLINT) "**/*.md" "#node_modules"

lenses:            ## every lens follows the format and cites a real section
	$(PYTHON) scripts/check_lenses.py

leaks:             ## no product or hardware vocabulary in the Markdown, no em-dash there nor in scripts/ and tests/
	$(PYTHON) scripts/check_leaks.py

links:             ## every relative link and anchor resolves
	$(PYTHON) scripts/check_links.py

prose:             ## no paragraph of architecture.md is longer than a person reads in one breath
	$(PYTHON) scripts/check_prose.py

toc:               ## the table of contents of architecture.md matches its headings
	$(PYTHON) scripts/gen_toc.py --check

version:           ## every copy of the release version agrees with .claude-plugin/plugin.json
	$(PYTHON) scripts/check_version.py

skills:            ## every skill has valid frontmatter and references files that exist
	$(PYTHON) scripts/check_skills.py

agents:            ## the reviewer agent mirrors the review template (decision words, report block, step count)
	$(PYTHON) scripts/check_agents.py

test:              ## the checkers and generators pass their own tests (needs pytest)
	$(PYTHON) -m pytest tests -q

plugin:            ## validate the plugin, marketplace, skills, and agents with Claude Code (skipped when claude is not installed)
	@if command -v claude >/dev/null 2>&1; then \
	  claude plugin validate . --strict && claude plugin validate skills --strict && claude plugin validate agents --strict; \
	else echo "plugin: claude not installed, skipped"; fi

gen-skills:        ## regenerate the review skills from the template and the lens files
	$(PYTHON) scripts/gen_skills.py

gen-skills-check:  ## fail when a generated skill is out of date
	$(PYTHON) scripts/gen_skills.py --check

gen-toc:           ## regenerate the table of contents of architecture.md
	$(PYTHON) scripts/gen_toc.py

clean:             ## remove tool caches
	rm -rf .markdownlint-cli2-cache node_modules .pytest_cache scripts/__pycache__ tests/__pycache__
