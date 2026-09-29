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
  carry it, and the total is kept as it goes. The stream shows what the
  session shows it, so a subagent the stream does not carry is held by
  Claude Code's own cap alone;
- the gate reruns. A gate run is a Bash call one of whose commands is the
  gate itself: the command's first words, after any variable settings,
  are the gate's words. `echo make check` and a commit message that names
  the gate are not gate runs. A run's outcome is read from the call's
  result only when the call's exit status is the gate's: nothing but `&&`
  follows the gate. Then it fails when the result is an error. A run that
  pipes the gate, or follows it with `;`, `||`, or `&`, is counted, and
  its outcome is not read: it neither fails nor passes. A failed run of a
  gate is followed by at most `max_gate_reruns` more; the phase is
  stopped when the last of them fails too. A run that passes ends the
  streak, so a later step that runs the gates again starts with its first
  run.

When either passes its bound, the watch sets `stop`, and the runtime
stops the phase there.

The watch also keeps each Agent call, the subagent tool (`Task` in older
releases), until its result comes. The Agent calls with no result when
the session's `result` event arrives are `pending`: the session ended
while a subagent it asked for had not answered, so the phase is
incomplete, however the result reads. A call whose result is the launch
notice ("Async agent launched ...") started its subagent in the
background: the notice answers the call, not the subagent, so the call
stays pending to the end of the session. With background tasks off, no
such launch should happen, and one that does means the setting did not
hold. What the call's input asks decides nothing: with background tasks
off, a call whose `run_in_background` is true runs in the foreground,
and its one result is the subagent's hand-back, which answers it.

After a phase, the harness reads its stream for the tool calls that name
the benchmark's run folders (`runs_named`): every finished tree of a
scenario, the judges' gaps, and the review's report. A call names them
when a string anywhere in its input holds `benchmark/runs` as whole path
segments: a Read, Grep, or Glob path, a Bash command, a WebFetch URL, a
subagent's prompt. A subagent's calls count as the main agent's do.

Between phases the harness runs short commands where the subject runs,
in its workspace: a checkpoint commit in the output folder after every
phase, that checkpoint's archive, the phase's milestone, which the
harness brings back and then removes from the workspace, the archive of
the last checkpoint, and the gates on the final tree. A run that resumes
another counts the files of the output folder it restored. The scripts
are below; each takes its paths as arguments, never spliced into the
script.

A checkpoint does not touch the output's branch, HEAD, or index. It
stages the tree as the subject left it, the files git tracks and those
it does not ignore, into an index of its own, commits that tree with the
message `checkpoint` and no parent, and keeps it under
`refs/checkpoints/<n>`. So a later phase finds the repository as the
phases before it left it, its own history holds no commit of the
harness's, and nothing in a commit names a phase or how it ended. The
phase before the checkpoint has ended, its processes with it, so a lock
git left in the repository is stale, and the checkpoint removes it. A
checkpoint whose tree holds no file says so, and the phase before it
left no tree.
"""

from __future__ import annotations

import json
import re
import shlex
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
# The bounds a phase can end at, besides finishing: money and time, the
# gate reruns, and a turn cap when the phase names one.
CAPS = ("spend", "time", "gate_reruns", "turns")
# The tool a session starts a subagent with; older releases of Claude Code name it Task.
AGENT_TOOLS = frozenset({"Agent", "Task"})
# How the result of an Agent call that started its subagent in the background opens: a notice, not the answer.
LAUNCH_NOTICE = "Async agent launched"
SUBTYPE_CAPS = {"error_max_turns": "turns", "error_max_budget_usd": "spend"}
# The multiples of a model's input price a cache read and a cache write are billed at.
CACHE_READ = 0.1
CACHE_WRITE = 1.25
CACHE_WRITE_1H = 2.0

# A command run in a folder of the workspace: the phase whose working
# folder is the output folder, and the gates. Exit 125: no such folder.
IN_FOLDER = 'cd -- "$1" || exit 125; shift; exec "$@"'
GATE = 'cd -- "$1" || exit 125; exec sh -c "$2"'
# The checkpoint of the output folder, the `n`th of the repeat: its tree
# committed under refs/checkpoints/<n>, with no identity of this
# machine's. Arguments: the folder, then n. The commit's id goes to
# stdout, after the line `empty` when the tree holds no file. Exit 3: no
# output folder; exit 4: git failed.
CHECKPOINT = (
    'cd -- "$1" 2>/dev/null || exit 3; '
    "{ [ -e .git ] || git init -q; } || exit 4; "
    "git_dir=$(git rev-parse --absolute-git-dir) || exit 4; "
    "find \"$git_dir\" -name '*.lock' -type f -exec rm -f -- {} + 2>/dev/null; "
    'index="$git_dir/checkpoint-index"; rm -f -- "$index"; '
    '{ [ ! -f "$git_dir/index" ] || cp -- "$git_dir/index" "$index"; } || exit 4; '
    'GIT_INDEX_FILE="$index" git add -A || exit 4; '
    'tree=$(GIT_INDEX_FILE="$index" git write-tree) || exit 4; rm -f -- "$index"; '
    "commit=$(git -c user.name=checkpoint -c user.email=checkpoint@localhost -c commit.gpgsign=false "
    'commit-tree "$tree" -m checkpoint) || exit 4; '
    'git update-ref "refs/checkpoints/$2" "$commit" || exit 4; '
    '[ -n "$(git ls-tree "$tree")" ] || echo empty; echo "$commit"'
)
# What the checkpoint prints when the tree it committed holds no file.
EMPTY = "empty"
# The archive of a checkpoint, into a path of the workspace. Arguments:
# the folder, the path, the checkpoint's commit.
ARCHIVE_SCRIPT = 'out="$PWD/$2"; mkdir -p -- "${out%/*}" && git -C "$1" archive --format=zip -o "$out" "$3"'
# A phase's milestone: its checkpoint archived into this path of the
# workspace, brought back, and removed with its folder (DROP) before the
# next phase starts, so no phase finds it.
MILESTONE = ".archive/milestone.zip"
# Where the handoff note is copied beside it, on a machine whose hidden
# folder is not this one's, so the collection brings it back too.
MILESTONE_NOTE = ".archive/HANDOFF.md"
# A path of the workspace removed. Argument: the path.
DROP = 'rm -rf -- "$1"'
# How many files the output folder holds, less its .git, a symlink counted
# as a file: for a milestone restored there. Argument: the folder. Exit 3:
# no such folder.
COUNT = 'cd -- "$1" 2>/dev/null || exit 3; find . -path ./.git -prune -o ! -type d -print | wc -l'


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


# The benchmark's run folders as a path or a URL names them: `benchmark/runs`
# as whole segments, so `swe-benchmark/runs` and `benchmark/runs-old` are not them.
RUNS = re.compile(r"(?<![\w.-])benchmark/runs(?![\w.-])")
# The most of a value a record keeps when it names a call.
NAMED_CHARS = 300


def runs_named(lines: list[str]) -> list[dict[str, str]]:
    """Each tool call of a stream whose input names the benchmark's run folders, in order.

    A call is its tool, its id, the key of its input that names them, and
    that value, cut to `NAMED_CHARS`. The input is read as the message
    shows it and as the line's `wire_tool_inputs` gives it, the call as
    sent, which can hold more: a leading `cd` the message leaves out. A
    call a stream carries in several lines counts once.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for event in events(lines):
        message = event.get("message")
        if event.get("type") != "assistant" or not isinstance(message, dict):
            continue
        wire = event.get("wire_tool_inputs")
        sent: dict[str, Any] = wire if isinstance(wire, dict) else {}
        for block in _content(message):
            if block.get("type") != "tool_use":
                continue
            ident = str(block.get("id") or "")
            if ident and ident in seen:
                continue
            strings = [*_strings(block.get("input"), ""), *_strings(sent.get(ident), "")]
            found = next((kv for kv in strings if RUNS.search(kv[1])), None)
            if found is None:
                continue
            seen.add(ident)
            key, value = found
            out.append({"tool": str(block.get("name") or ""), "id": ident, "key": key, "value": value[:NAMED_CHARS]})
    return out


def _strings(value: Any, key: str) -> list[tuple[str, str]]:
    """Every string in a tool call's input, each with its key: `command`, `edits.0.new_string`."""
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(v, f"{key}.{k}" if key else str(k))]
    if isinstance(value, list):
        return [s for at, v in enumerate(value) for s in _strings(v, f"{key}.{at}" if key else str(at))]
    return []


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


def model_costs(result: dict[str, Any] | None) -> dict[str, float]:
    """Each model a session's result reports under `modelUsage`, with its cost in US dollars as Claude Code prices it.

    A session's helpers can run on another model than its main agent, so
    the result names each. A model with no `costUSD` is left out.
    """
    usage = result.get("modelUsage") if result else None
    out: dict[str, float] = {}
    for model, figures in (usage if isinstance(usage, dict) else {}).items():
        cost = figures.get("costUSD") if isinstance(figures, dict) else None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            out[str(model)] = round(float(cost), 6)
    return out


def cap_of(result: dict[str, Any] | None) -> str | None:
    """The bound a result says stopped the session, or None."""
    return SUBTYPE_CAPS.get(str(result.get("subtype"))) if result else None


# The shell's control operators, as `shlex` splits them, and those that
# let the gate's exit status be the call's when they follow the gate.
OPERATORS = frozenset({"&&", "||", "|", "|&", ";", ";;", "&", "(", ")"})
KEEPS_STATUS = frozenset({"&&", ")"})
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def commands(command: str) -> list[tuple[list[str], list[str]]]:
    """Each simple command of a shell command line: its words, and the operators that follow it to the end.

    A line of its own is a command of its own, as `;` makes one. A line
    that does not split as shell words is left out.
    """
    tokens: list[str] = []
    for line in command.split("\n"):
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            words = list(lexer)
        except ValueError:
            words = []
        if words:
            tokens += [*words, ";"]
    # Every line ends in a `;` of its own; the last line's is no operator of the command's.
    out: list[tuple[list[str], list[str]]] = []
    current: list[str] = []
    for at, token in enumerate(tokens):
        if token in OPERATORS:
            if current:
                out.append((current, [t for t in tokens[at:-1] if t in OPERATORS]))
            current = []
        else:
            current.append(token)
    return out


def gate_runs(command: str, gates: list[str]) -> list[tuple[str, bool]]:
    """The gates a command runs as commands of their own, each with whether the call's status is that run's."""
    out: list[tuple[str, bool]] = []
    for words, after in commands(command):
        while words and ASSIGNMENT.match(words[0]):
            words = words[1:]
        for gate in gates:
            wanted = gate.split()
            if words[: len(wanted)] == wanted:
                out.append((gate, all(op in KEEPS_STATUS for op in after)))
    return out


def result_text(block: dict[str, Any]) -> str:
    """A tool result's text: its content as a string, or its text items joined, stripped at the start."""
    content = block.get("content")
    if isinstance(content, list):
        content = "".join(str(i.get("text") or "") for i in content if isinstance(i, dict))
    return content.lstrip() if isinstance(content, str) else ""


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
    `gate_runs()`, and `pending`, the Agent calls with no result when
    the session's result came.
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
        self.gates = list(gates or [])
        self.max_gate_reruns = max_gate_reruns
        self.stop = threading.Event()
        self.capped: str | None = None
        self.unpriced: set[str] = set()
        # Each message's model, its usage at its largest, and what that cost; and the total, kept as it goes.
        self._messages: dict[str, tuple[str, dict[str, Any]]] = {}
        self._costs: dict[str, float] = {}
        self._total = 0.0
        self._pending: dict[str, list[tuple[str, bool]]] = {}
        self._gates = {g: {"runs": 0, "failed": 0, "unread": 0, "streak": 0} for g in self.gates}
        # Each Agent call with no result yet, by its id, with what it was asked; a call whose result is a launch
        # notice stays; and those still open when the session's result came.
        self._agents: dict[str, str] = {}
        self.pending: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def feed(self, stream: str, line: str) -> None:
        """Read one line the phase wrote; stop it when it passes a bound."""
        if stream != "out":
            return
        event = parse(line)
        if event is None:
            return
        if event.get("type") == "result":
            with self._lock:
                self.pending = [{"id": k, "description": v} for k, v in self._agents.items()]
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
                    if block.get("type") == "tool_use" and block.get("name") in AGENT_TOOLS and isinstance(block.get("id"), str):
                        given = block.get("input")
                        asked: dict[str, Any] = given if isinstance(given, dict) else {}
                        self._agents[block["id"]] = str(asked.get("description") or asked.get("subagent_type") or "")
            elif event.get("type") == "user":
                for block in _content(message):
                    if block.get("type") != "tool_result":
                        continue
                    # A background launch's result is its notice; the subagent has not answered, so the call stays pending.
                    if not result_text(block).startswith(LAUNCH_NOTICE):
                        self._agents.pop(str(block.get("tool_use_id")), None)
                    if block.get("tool_use_id") in self._pending:
                        for gate, read in self._pending.pop(block["tool_use_id"]):
                            self._ran(gate, failed=block.get("is_error") is True, read=read)

    def gates_in(self, tool_input: Any) -> list[tuple[str, bool]]:
        """The gates a Bash call's command runs, each with whether the call's result is that run's outcome."""
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        return gate_runs(command, self.gates) if isinstance(command, str) else []

    def _ran(self, gate: str, failed: bool, read: bool) -> None:
        counts = self._gates[gate]
        counts["runs"] += 1
        if not read:
            counts["unread"] += 1
            return
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
            before = merged.get(name)
            if isinstance(value, dict):
                earlier = before if isinstance(before, dict) else {}
                merged[name] = {**earlier, **{k: max(_count(v), _count(earlier.get(k))) for k, v in value.items()}}
            elif not isinstance(before, dict):
                merged[name] = max(_count(value), _count(before))
        self._messages[key] = (model, merged)
        cost = self.cost(model, merged)
        self._total += cost - self._costs.get(key, 0.0)
        self._costs[key] = cost
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

    def cost(self, model: str, usage: dict[str, Any]) -> float:
        """What one message's usage costs at the model's price."""
        price = self.price(model)
        if price is None:
            return 0.0
        split = usage.get("cache_creation")
        hour = _count(split.get("ephemeral_1h_input_tokens")) if isinstance(split, dict) else 0
        written = _count(usage.get("cache_creation_input_tokens"))
        return (
            _count(usage.get("input_tokens")) * price["input"]
            + _count(usage.get("cache_read_input_tokens")) * price["input"] * CACHE_READ
            + max(written - hour, 0) * price["input"] * CACHE_WRITE
            + hour * price["input"] * CACHE_WRITE_1H
            + _count(usage.get("output_tokens")) * price["output"]
        ) / 1e6

    @property
    def estimated_usd(self) -> float:
        """What the messages seen so far cost at the matrix's prices."""
        return round(max(self._total, 0.0), 6)

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
        """Each gate's runs, its failures, the runs whose outcome was not read, and whether its last read run failed."""
        return {
            gate: {"runs": c["runs"], "failed": c["failed"], "unread": c["unread"], "failing": c["streak"] > 0}
            for gate, c in self._gates.items()
        }
