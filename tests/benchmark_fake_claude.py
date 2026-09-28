"""A Claude Code stand-in for the phase tests: it speaks stream-json and reports what it saw.

Its prompt carries what it does, as `DO {json}` on the first line:

- `write`: files to write, by a path relative to where it starts;
- `read`: files to read before it writes, by a path relative to where it starts; each one's text, or null, is in
  its answer under `read`;
- `tree`: a folder to list before it writes, by a path relative to where it starts; every file under it, by its
  path in it, with `.git` left out, is in its answer under `tree`;
- `git`: git commands to run where it starts, after the writes, each as its words; what each printed is in its answer;
- `touch`: files to leave behind empty after the git commands, such as a lock;
- `note`: text for the handoff note, at the path the hint names;
- `messages`: assistant messages as `[id, model, usage]`, each written twice, as a stream can;
- `bash`: Bash calls as `[command, failed]`, each a tool use and its result;
- `agents`: Agent calls as `[id, description, answered]`, each a tool use, and its result only when answered;
- `launched`: Agent calls as `[id, description]` that start their subagent in the background, as Claude Code
  writes them: the call with `run_in_background` set to "true", the launch notice as its result, then the
  main agent's text as it ends its turn to wait;
- `handed_back`: Agent calls as `[id, description]` that ask for the background but run in the foreground, as
  Claude Code writes them with background tasks off: the call with `run_in_background` set to "true", its
  `task_started` line, not backgrounded, its `task_notification` once the helper is done, then its one result,
  the helper's hand-back;
- `sleep`: seconds to wait after the messages, so the harness can stop it;
- `subtype`, `is_error`, `cost`, `usage`: what its result line says; `result: false` writes none;
- `models`: each model's cost in its result's `modelUsage`, by name;
- `exit`: its exit code.

Its answer, the result's `result`, is a JSON object of what it saw: its
HOME and what was in it, where it started, the session it resumed, the
handoff note it found, CLAUDE_CODE_DISABLE_BACKGROUND_TASKS, and its
arguments. It leaves a file of its own in
its HOME, so a later session in the same HOME finds it.
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("9.9.9 (Claude Code)")
    sys.exit(0)

prompt = args[args.index("-p") + 1]
match = re.match(r"DO (\{.*?\})\s*$", prompt.split("\n")[0])
todo = json.loads(match.group(1)) if match else {}
resumed = args[args.index("--resume") + 1] if "--resume" in args else None
session = resumed or str(uuid.uuid4())
home = os.environ.get("HOME", "")
hint = re.search(r"handoff note at (\S+) as you work", prompt)
note_path = hint.group(1) if hint else None
seen = {
    "home": home,
    "home_files": sorted(os.listdir(home)) if home and os.path.isdir(home) else [],
    "cwd": os.getcwd(),
    "resumed": resumed,
    "note_path": note_path,
    "note": Path(note_path).read_text(encoding="utf-8") if note_path and os.path.exists(note_path) else None,
    "handoff_in_reach": [p for p in ("HANDOFF.md", "../HANDOFF.md") if os.path.exists(p)],
    "background_tasks": os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"),
    "args": args,
}


def emit(event):
    print(json.dumps(event), flush=True)


emit({"type": "system", "subtype": "init", "session_id": session, "model": "claude-opus-5-5"})
seen["read"] = {path: Path(path).read_text(encoding="utf-8") if Path(path).is_file() else None for path in todo.get("read", [])}
if "tree" in todo:
    top = Path(todo["tree"])
    seen["tree"] = sorted(
        p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file() and ".git" not in p.relative_to(top).parts
    )
for path, text in todo.get("write", {}).items():
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
seen["git"] = [
    subprocess.run(["git", *words], capture_output=True, text=True, check=False).stdout.strip() for words in todo.get("git", [])
]
for path in todo.get("touch", []):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).touch()
if "note" in todo and note_path:
    Path(note_path).write_text(todo["note"], encoding="utf-8")
if home:
    (Path(home) / f"was-here-{session[:8]}").write_text("x", encoding="utf-8")
for message_id, model, usage in todo.get("messages", []):
    message = {"id": message_id, "model": model, "usage": usage, "content": [{"type": "text", "text": "working"}]}
    for _ in range(2):
        emit({"type": "assistant", "message": message, "session_id": session})
for number, (command, failed) in enumerate(todo.get("bash", [])):
    call = {"type": "tool_use", "id": f"toolu_{number}", "name": "Bash", "input": {"command": command}}
    emit({"type": "assistant", "message": {"id": f"msg_bash_{number}", "content": [call]}, "session_id": session})
    answer = {"type": "tool_result", "tool_use_id": f"toolu_{number}", "content": "out", "is_error": failed}
    emit({"type": "user", "message": {"role": "user", "content": [answer]}, "session_id": session})
for call_id, description, answered in todo.get("agents", []):
    call = {"type": "tool_use", "id": call_id, "name": "Agent", "input": {"description": description, "prompt": "p"}}
    emit({"type": "assistant", "message": {"id": f"msg_{call_id}", "content": [call]}, "session_id": session})
    if answered:
        answer = {"type": "tool_result", "tool_use_id": call_id, "content": "done"}
        emit({"type": "user", "message": {"role": "user", "content": [answer]}, "session_id": session})
for call_id, description in todo.get("launched", []):
    asked = {"description": description, "prompt": "p", "run_in_background": "true", "subagent_type": "general-purpose"}
    call = {"type": "tool_use", "id": call_id, "name": "Agent", "input": asked}
    emit({"type": "assistant", "message": {"id": f"msg_{call_id}", "content": [call]}, "session_id": session})
    notice = f"Async agent launched successfully. The agent is working in the background. agentId: a{call_id[-6:]}"
    answer = {"type": "tool_result", "tool_use_id": call_id, "content": [{"type": "text", "text": notice}]}
    emit({"type": "user", "message": {"role": "user", "content": [answer]}, "session_id": session})
for call_id, description in todo.get("handed_back", []):
    asked = {"description": description, "subagent_type": "general-purpose", "run_in_background": "true", "prompt": "p"}
    call = {"type": "tool_use", "id": call_id, "name": "Agent", "input": asked, "caller": {"type": "direct"}}
    message = {"id": f"msg_{call_id}", "content": [call]}
    emit({"type": "assistant", "message": message, "parent_tool_use_id": None, "session_id": session})
    task = {"task_id": f"a{call_id[-6:]}", "tool_use_id": call_id, "session_id": session}
    started = {"description": description, "subagent_type": "general-purpose", "is_backgrounded": False}
    emit({"type": "system", "subtype": "task_started", **task, **started})
    emit({"type": "system", "subtype": "task_notification", **task, "status": "completed", "summary": "Done."})
    report = (
        "[Subagent hand-back] The text below is the final report of a subagent this session delegated to.\n"
        f"  Done.\nagentId: a{call_id[-6:]}\n<usage>subagent_tokens: 1000\ntool_uses: 4\nduration_ms: 60000</usage>"
    )
    answer = {"tool_use_id": call_id, "type": "tool_result", "content": [{"type": "text", "text": report}]}
    handed = {"status": "completed", "agentId": f"a{call_id[-6:]}", "agentType": "general-purpose"}
    emit({"type": "user", "message": {"role": "user", "content": [answer]}, "session_id": session, "tool_use_result": handed})
if todo.get("launched"):
    waiting = {"type": "text", "text": "The helpers are working in the background; I will wait for them."}
    emit({"type": "assistant", "message": {"id": "msg_wait", "content": [waiting]}, "session_id": session})
time.sleep(todo.get("sleep", 0))
if todo.get("result", True):
    result = {
        "type": "result",
        "subtype": todo.get("subtype", "success"),
        "is_error": todo.get("is_error", False),
        "num_turns": 3,
        "session_id": session,
        "total_cost_usd": todo.get("cost", 0.25),
        "usage": todo.get("usage", {"input_tokens": 10, "output_tokens": 20}),
        "modelUsage": {m: {"costUSD": c} for m, c in todo["models"].items()} if "models" in todo else {"claude-opus-5-5": {}},
    }
    if result["subtype"] == "success":
        result["result"] = json.dumps(seen)
    emit(result)
sys.exit(todo.get("exit", 0))
