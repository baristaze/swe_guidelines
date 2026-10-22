# ADR 0105: The unit suite runs on every core

**Status**: accepted (2026-10-21)

## Context

The unit suite runs over the memory roots and the pure rules, with no
stack under it. The fast gate waits on it: on a person's machine before
each push, and in CI on every pull request. On one process it uses one
core of a machine that has several, and a runner GitHub gives a public
repository has four.

pytest-xdist runs a suite on several worker processes. Each worker
collects the whole suite itself, and the run starts only when every
worker collected the same tests, under the same ids, in the same order.

## Decision

**`make test-unit` runs one worker per core.** It runs pytest with
`-n auto`, on a person's machine and in CI alike, and
`PYTEST_XDIST_AUTO_NUM_WORKERS` sets the count on a machine other work
shares. pytest-xdist is a dev dependency, locked in `uv.lock`. The
integration and telemetry suites keep one process: their tests share
one local stack and empty its tables.

**A unit test holds nothing another test can reach.** A worker runs the
tests it is handed, in an order that changes from run to run, while the
other workers run theirs. A test builds what it changes inside itself or
in a fixture of function scope: its memory root, its files under
`tmp_path`, its environment through `monkeypatch`. A value a module
shares is one no test changes.

**A test's id is fixed at collection.** A parametrized value that
becomes part of an id is a literal, or derived from one. It is never
drawn at import (`new_id()`, a clock, a random draw), and never read in
a set's order, which changes from process to process. A value drawn per
run goes inside the test, or behind an explicit `id`.

**Each run names its ten slowest tests.** `--durations=10` closes every
run of the unit suite, so a slow test shows on the run that made it
slow.

## Consequences

The unit suite's wall time falls with the cores a machine has, and the
fast gate's with it.

A test that leans on another's state fails under the workers, or fails
some of the time. The fix is in the test, never a return to one process.
An id drawn at collection stops the run before any test starts, and
xdist prints the two collections that differ.

The workers are the Makefile's. `uv run pytest`, as a person runs one
test or stops in a debugger, keeps one process.
