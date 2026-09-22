"""arch-check: the static checker of the Software Design and Architecture Guidelines.

It decides the lenses that are syntactic (an import direction, a class
shape, a signature) by parsing a project's source with `ast`. It never
imports the code it checks. Standard library only.

`__version__` is the guideline release this copy ships with;
`scripts/check_version.py` holds it equal to `.claude-plugin/plugin.json`.
"""

__version__ = "0.28.0"
