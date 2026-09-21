"""The rules, one module per concern.

Every module here is imported by `arch_check.registry.load`, and each
rule in it registers itself with `@rule(...)`. A module whose name
starts with `_` is a helper and is not imported by the loader.
"""
