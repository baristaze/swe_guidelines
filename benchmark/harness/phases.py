"""A subject in phases: what the harness reads from a session's stream, and what it runs between phases.

A skill subject runs `claude -p` with `--output-format stream-json
--verbose`, so every turn of the session is one JSON line in
`streams/cli.jsonl`. The last line of a session is its result: the
answer, the models that answered, the session's id, what Claude Code
says the session cost, and, when a bound stopped it, which bound. A
result's `subtype` is `success`, or it names the bound: `error_max_turns`
or `error_max_budget_usd`.

A phase is bounded by count and by spend. Claude Code holds the turn cap
(`--max-turns`) and the spend cap (`--max-budget-usd`) itself. `Watch`
reads the stream as it is written and holds two bounds of its own:

- the spend, priced from the usage of every assistant message the stream
  carries. The price is the model's in the matrix. A cache read is priced
  at a tenth of the input price, a cache write at 1.25 times it, or twice
  it for a write the usage names as a one-hour write. A model the matrix
  has no price for is priced at the matrix's dearest Anthropic model, so
  the estimate errs high. A message is counted once however many lines
  carry it. The stream shows what the session shows it, so a subagent the
  stream does not carry is held by Claude Code's own cap alone;
- the gate reruns. A gate run is a Bash call whose command runs a command
  the scenario lists under `gates`, read from the call in the stream, and
  it fails when its result is an error. A failed run of a gate is followed
  by at most `max_gate_reruns` more; the phase is stopped when the last of
  them fails too. A run that passes ends the streak, so a later step that
  runs the gates again starts with its first run.

When either passes its bound, the watch sets `stop`, and the runtime
stops the phase there.

Between phases the harness runs short commands where the subject runs,
in its workspace: a checkpoint commit in the output folder after every
phase, the archive of the last commit, and the gates on the final tree.
The scripts are below; each takes its paths as arguments, never spliced
into the script.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

# The workspace's layout besides the output folder. The handoff note a
# hinted phase keeps sits beside the output folder, never in it, and the
# harness takes it out of the workspace while a phase without a hint
# runs. The archive is made after the last phase, beside it too.
HANDOFF = "HANDOFF.md"
ARCHIVE = ".archive/output.zip"
# What a hinted phase is told, with the note's path as that phase sees it.
HINT = (
    "Keep a handoff note at {path} as you work: what is done, what you decided, "
    "and what is left. If it is there when you start, an earlier session wrote it; read it first."
)
# The bounds a phase can end at, besides finishing.
CAPS = ("turns", "spend", "gate_reruns")
SUBTYPE_CAPS = {"error_max_turns": "turns", "error_max_budget_usd": "spend"}
# The multiples of a model's input price a cache read and a cache write are billed at.
CACHE_READ = 0.1
CACHE_WRITE = 1.25
CACHE_WRITE_1H = 2.0

# A command run in a folder of the workspace: the phase whose working
# folder is the output folder, and the gates. Exit 125: no such folder.
IN_FOLDER = 'cd -- "$1" || exit 125; shift; exec "$@"'
GATE = 'cd -- "$1" || exit 125; exec sh -c "$2"'
# The checkpoint: every change in the output folder, committed, with no
# hook of the tree's run and no identity of this machine's. The commit's
# id goes to stdout. Exit 3: no output folder; exit 4: git failed.
CHECKPOINT = (
    'cd -- "$1" 2>/dev/null || exit 3; '
    "{ [ -e .git ] || git init -q; } && git add -A && "
    "git -c user.name=checkpoint -c user.email=checkpoint@localhost -c commit.gpgsign=false "
    '-c core.hooksPath=/dev/null commit -q --allow-empty --no-verify -m "$2" || exit 4; '
    "git rev-parse HEAD"
)
# The archive of the output folder's last commit, into a path of the workspace.
ARCHIVE_SCRIPT = 'out="$PWD/$2"; mkdir -p -- "${out%/*}" && git -C "$1" archive --format=zip -o "$out" HEAD'


def events(lines: list[str]) -> list[dict[str, Any]]:
    """Every line of a stream that is a JSON object, in order."""
    out: list[dict[str, Any]] = []
    for line in lines:
        data = parse(line)
        if data is not None:
            out.append(data)
    return out


def parse(line: str) -> dict[str, Any] | None:
    """A stream line as a JSON object, or None when it is not one."""
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def final_result(lines: list[str]) -> dict[str, Any] | None:
    """The session's result: the last line whose `type` is `result`, or None when it wrote none."""
    for line in reversed(lines):
        data = parse(line)
        if data is not None and data.get("type") == "result":
            return data
    return None


def session_id(lines: list[str]) -> str | None:
    """The id of the session the lines belong to: the result's, else the last one a line names."""
    for line in reversed(lines):
        data = parse(line)
        value = data.get("session_id") if data else None
        if isinstance(value, str) and value:
            return value
    return None


def cap_of(result: dict[str, Any] | None) -> str | None:
    """The bound a result says stopped the session, or None."""
    return SUBTYPE_CAPS.get(str(result.get("subtype"))) if result else None


def gate_pattern(gate: str) -> re.Pattern[str]:
    """A gate's words, found as whole shell words in a command: `make check` in `cd x && make check`, not in `make checks`."""
    words = r"\s+".join(re.escape(w) for w in gate.split())
    return re.compile(rf"(?:^|[\s;&|(]){words}(?=$|[\s;&|)])")


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _content(message: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = message.get("content")
    return [b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else []


class Watch:
    """The bounds the harness holds over one phase, read from its stream as it is written.

    `feed` takes each line the phase writes. `stop` is set once a bound
    is passed, and `capped` names it: `spend` or `gate_reruns`. The
    figures stay readable after the phase: `estimated_usd`, `usage()`,
    and `gate_runs()`.
    """

    def __init__(
        self,
        max_usd: float | None,
        prices: dict[str, dict[str, float]],
        gates: list[str] | None = None,
        max_gate_reruns: int = 3,
    ) -> None:
        self.max_usd = max_usd
        self.prices = dict(prices)
        # The dearest model the matrix prices, for a model it does not.
        self.dearest = max(self.prices.values(), key=lambda p: (p["input"], p["output"])) if self.prices else None
        self.patterns = {g: gate_pattern(g) for g in gates or []}
        self.max_gate_reruns = max_gate_reruns
        self.stop = threading.Event()
        self.capped: str | None = None
        self.unpriced: set[str] = set()
        self._messages: dict[str, tuple[str, dict[str, Any]]] = {}
        self._pending: dict[str, list[str]] = {}
        self._gates = {g: {"runs": 0, "failed": 0, "streak": 0} for g in self.patterns}
        self._lock = threading.Lock()

    def feed(self, stream: str, line: str) -> None:
        """Read one line the phase wrote; stop it when it passes a bound."""
        if stream != "out":
            return
        event = parse(line)
        if event is None:
            return
        message = event.get("message")
        if not isinstance(message, dict):
            return
        with self._lock:
            if event.get("type") == "assistant":
                self._spend(message)
                for block in _content(message):
                    if block.get("type") == "tool_use" and block.get("name") == "Bash":
                        gates = self.gates_in(block.get("input"))
                        if gates and isinstance(block.get("id"), str):
                            self._pending[block["id"]] = gates
            elif event.get("type") == "user":
                for block in _content(message):
                    if block.get("type") == "tool_result" and block.get("tool_use_id") in self._pending:
                        for gate in self._pending.pop(block["tool_use_id"]):
                            self._ran(gate, failed=block.get("is_error") is True)

    def gates_in(self, tool_input: Any) -> list[str]:
        """The gates a Bash call's command runs, in the scenario's order."""
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str):
            return []
        return [g for g, pattern in self.patterns.items() if pattern.search(command)]

    def _ran(self, gate: str, failed: bool) -> None:
        counts = self._gates[gate]
        counts["runs"] += 1
        if not failed:
            counts["streak"] = 0
            return
        counts["failed"] += 1
        counts["streak"] += 1
        if counts["streak"] > self.max_gate_reruns:
            self._cap("gate_reruns")

    def _spend(self, message: dict[str, Any]) -> None:
        usage = message.get("usage")
        key = message.get("id")
        if not isinstance(usage, dict) or not isinstance(key, str):
            return
        # One message can reach the stream in several lines; its usage counts once, at its largest.
        model = str(message.get("model") or "")
        _, seen = self._messages.get(key, (model, {}))
        merged = dict(seen)
        for name, value in usage.items():
            if isinstance(value, dict):
                merged[name] = {k: max(_count(v), _count(merged.get(name, {}).get(k))) for k, v in value.items()}
            else:
                merged[name] = max(_count(value), _count(seen.get(name)))
        self._messages[key] = (model, merged)
        if self.max_usd is not None and self.estimated_usd > self.max_usd:
            self._cap("spend")

    def _cap(self, bound: str) -> None:
        if self.capped is None:
            self.capped = bound
        self.stop.set()

    def price(self, model: str) -> dict[str, float] | None:
        """The model's price: its own, else that of the matrix model it is a dated release of, else the dearest."""
        if model in self.prices:
            return self.prices[model]
        for name, price in self.prices.items():
            if model.startswith(f"{name}-"):
                return price
        self.unpriced.add(model or "unknown")
        return self.dearest

    @property
    def estimated_usd(self) -> float:
        """What the messages seen so far cost at the matrix's prices."""
        total = 0.0
        for model, usage in self._messages.values():
            price = self.price(model)
            if price is None:
                continue
            split = usage.get("cache_creation")
            hour = _count(split.get("ephemeral_1h_input_tokens")) if isinstance(split, dict) else 0
            written = _count(usage.get("cache_creation_input_tokens"))
            total += (
                _count(usage.get("input_tokens")) * price["input"]
                + _count(usage.get("cache_read_input_tokens")) * price["input"] * CACHE_READ
                + max(written - hour, 0) * price["input"] * CACHE_WRITE
                + hour * price["input"] * CACHE_WRITE_1H
                + _count(usage.get("output_tokens")) * price["output"]
            ) / 1e6
        return round(total, 6)

    def usage(self) -> dict[str, int]:
        """The tokens of the messages seen so far, in the shape a result's usage is recorded in."""
        fields = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
        sums = {name: sum(_count(u.get(name)) for _, u in self._messages.values()) for name in fields}
        if not any(sums.values()):
            return {}
        return {
            "input_tokens": sums["input_tokens"] + sums["cache_read_input_tokens"] + sums["cache_creation_input_tokens"],
            "output_tokens": sums["output_tokens"],
            "cache_read_input_tokens": sums["cache_read_input_tokens"],
            "cache_creation_input_tokens": sums["cache_creation_input_tokens"],
        }

    def gate_runs(self) -> dict[str, dict[str, Any]]:
        """Each gate's runs and failures, and whether its last run failed."""
        return {gate: {"runs": c["runs"], "failed": c["failed"], "failing": c["streak"] > 0} for gate, c in self._gates.items()}
