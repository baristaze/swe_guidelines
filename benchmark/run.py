#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml==6.0.3",
#   "jsonschema==4.26.0",
#   "pydantic==2.13.5",
#   "anthropic==1.7.0",
#   "openai==3.17.0",
#   "google-genai==2.24.0",
#   "websockets==16.1.1",
# ]
# ///
# Pinned exactly: the harness installs in a step that holds no key, and the
# step that holds the keys runs what was installed there, never a newer one.
"""Run one scenario and have the frontier models judge what came out.

    uv run benchmark/run.py --scenario explain-tenancy --providers 7 --effort medium --repeat 1
    uv run benchmark/run.py list

Everything a run produced lands in one folder under `--out`: the
resolved scenario, the streams as they were written, the artifact, one
file per judgement, `results.json` in the schema, and `report.md`.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

BENCHMARK = Path(__file__).resolve().parent
ROOT = BENCHMARK.parent
if str(BENCHMARK) not in sys.path:
    sys.path.insert(0, str(BENCHMARK))

from harness import evidence as E  # noqa: E402
from harness import judge as J  # noqa: E402
from harness import providers as P  # noqa: E402
from harness import results as R  # noqa: E402
from harness import runtime as RT  # noqa: E402
from harness import scenario as S  # noqa: E402
from harness.capture import CliStream, FrameSink  # noqa: E402

SCENARIOS = BENCHMARK / "scenarios"
SCHEMA = BENCHMARK / "schema" / "result.schema.json"
MODELS = BENCHMARK / "models.yaml"
DEFAULT_OUT = BENCHMARK / "runs"
# What a subject inherits beyond its private HOME and TMPDIR and its key.
PASSTHROUGH = ["PATH", "LANG", "LC_ALL", "SHELL", "TERM", "USER"]


def subject_keys(scn: S.Scenario) -> list[str]:
    """The key names the subject's command reads. A `qa` subject runs no command."""
    return [] if scn.kind == "qa" else list(RT.SUBJECT_KEYS)


def subject_env(scn: S.Scenario, source: dict[str, str] | None = None) -> dict[str, str]:
    """The environment the subject is spawned from: no judge's key is ever in it.

    `claude -p` reads ANTHROPIC_API_KEY. The subject's value for it comes
    from SUBJECT_ANTHROPIC_API_KEY, a key of its own, so no judge's key is
    copied into the subject's environment, on any runtime.
    """
    source = dict(os.environ) if source is None else source
    env = RT.passthrough_env(PASSTHROUGH, source)
    for name in subject_keys(scn):
        value = source.get(RT.SUBJECT_KEYS[name])
        if value:
            env[name] = value
    return RT.scrub(env, source)[0]


def new_run_dir(out: Path, scenario: str) -> tuple[str, Path]:
    """A run folder no other run has, and its name.

    The name starts with the second and the scenario, so the folders sort
    by time. A random suffix tells apart two runs of one scenario started
    in the same second, and the folder is created only when it is not
    there yet, so no run ever writes into another's.
    """
    out.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{scenario}-{uuid.uuid4().hex[:8]}"
        try:
            (out / run_id).mkdir()
        except FileExistsError:
            continue
        return run_id, out / run_id


def git_sha(path: Path) -> str:
    """The commit of a checkout, or an empty string when it is not one."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def plugin_name(root: Path) -> str:
    """The plugin name of the checkout, which is how a skill is addressed."""
    manifest = root / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            return str(json.loads(manifest.read_text(encoding="utf-8")).get("name") or "swe-guidelines")
        except json.JSONDecodeError:
            pass
    return "swe-guidelines"


def subject_prompt(scn: S.Scenario, target: str | None) -> str:
    """The prompt with the target in it, as the subject sees the target.

    `{target}` in the prompt is replaced with the path. A prompt that does
    not name it gets one sentence saying where the target is. The subject
    runs in its own empty workspace, so a target it is not told about is
    a target it never reads.
    """
    prompt = scn.subject.prompt
    if "{target}" in prompt:
        if not target:
            raise S.ScenarioError(f"scenario {scn.name}: the prompt names {{target}} and the run has no target")
        return prompt.replace("{target}", target)
    if target:
        return f"{prompt}\n\nThe checkout to work on is at {target}. Read it; do not change it."
    return prompt


def subject_model(scn: S.Scenario, flag: str | None, matrix: dict[str, Any]) -> str | None:
    """The model the subject runs on: the flag, else the scenario's, else the matrix's first.

    A skill runs `claude -p`, whose default model moves under the run; a
    score is only comparable to another on the same model, so the model is
    always pinned. A command subject runs what its argv says, and has no
    model unless the flag names one.
    """
    if flag:
        return flag
    if scn.subject.model:
        return scn.subject.model
    if scn.kind == "command":
        return None
    provider = "anthropic" if scn.kind == "skill" else P.name(P.parse(scn.subject.provider or "anthropic"))
    return J.models_for(matrix, provider)[0] or None


def subject_argv(
    scn: S.Scenario, name: str, plugin: str | None, target: str | None, claude: str = "claude", model: str | None = None
) -> list[str]:
    """The command a subject of each kind runs. `qa` runs no command.

    `plugin` and `target` are paths as the subject sees them, which the
    runtime answers: this machine's paths on the host, the mount points
    in a container, the configured paths on another machine.
    """
    if scn.kind == "command":
        return [w.replace("{target}", target or "").replace("{plugin}", plugin or "") for w in scn.subject.argv]
    if scn.kind == "qa":
        return []
    prompt = f"/{name}:{scn.subject.skill} {subject_prompt(scn, target)}".strip()
    argv = [
        claude,
        "-p",
        prompt,
        "--plugin-dir",
        str(plugin),
        "--output-format",
        "json",
        "--max-turns",
        str(scn.subject.max_turns),
    ]
    if model:
        argv += ["--model", model]
    if target:
        # The workspace is the subject's working directory; the target is outside it.
        argv += ["--add-dir", target]
    if scn.subject.allowed_tools:
        argv += ["--allowedTools", ",".join(scn.subject.allowed_tools)]
    return argv


def read_envelope(stdout: str) -> tuple[str, list[str], bool]:
    """The answer, the models, and the error flag of a `claude --output-format json` envelope.

    The models are the keys of `modelUsage`: the models that answered, read
    back so a run records what ran and not only what it asked for. Output
    that is not an envelope is the answer as it is, with no model and no
    error.
    """
    text = stdout.strip()
    if not text.startswith("{"):
        return stdout, [], False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return stdout, [], False
    if not isinstance(data, dict) or not isinstance(data.get("result"), str):
        return stdout, [], False
    usage = data.get("modelUsage")
    models = sorted(str(m) for m in usage) if isinstance(usage, dict) else []
    return data["result"], models, data.get("is_error") is True


def stdout_of(stream_path: Path) -> str:
    """Everything the subject wrote to stdout, in order."""
    return "\n".join(r["line"] for r in CliStream.read(stream_path) if r.get("s") == "out")


def context_text(scn: S.Scenario) -> str:
    """The context files of a `qa` subject, each under its own heading.

    A path is read from the scenario file's folder, as every path of a
    scenario is. A file that is not there is a ScenarioError: a subject
    that silently lost its context answers a different question.
    """
    out = ""
    for value in scn.subject.context:
        file = scn.resolve(value)
        if file is None or not file.is_file():
            raise S.ScenarioError(f"scenario {scn.name}: subject.context {value!r} is not a file ({file})")
        out += f"\n\n## Context: {file.name}\n\n{file.read_text(encoding='utf-8')}"
    return out


def run_subject_qa(
    scn: S.Scenario, streams: CliStream, env: dict[str, str], matrix: dict[str, Any], effort: str, model: str
) -> tuple[RT.ExitStatus, str]:
    """A `qa` subject: one provider model answers the prompt itself, at the effort the run names."""
    provider = P.parse(scn.subject.provider or "anthropic")
    key = P.key(provider, env)
    started = time.monotonic()
    if not key:
        streams.note(f"[qa] no key for {P.name(provider)}")
        return RT.ExitStatus(code=2, duration_s=time.monotonic() - started), ""
    prompt = scn.subject.prompt + context_text(scn)
    streams.note(f"[qa] {P.name(provider)} {model}")
    try:
        text, usage = J.ask(provider, model, prompt, key, J.effort_for(matrix, P.name(provider), effort))
    except Exception as exc:
        streams.note(f"[qa] {type(exc).__name__}: {exc}")
        return RT.ExitStatus(code=1, duration_s=time.monotonic() - started), ""
    for line in text.split("\n"):
        streams.write("out", line)
    streams.note(f"[qa] usage {json.dumps(usage)}")
    return RT.ExitStatus(code=0, duration_s=time.monotonic() - started), text


def collect_files(rt: RT.BaseRuntime, globs: list[str], art_dir: Path, index: int) -> tuple[list[str], list[str]]:
    """Copy the collected files of one repeat and return their paths and their text.

    A file keeps its path inside the workspace, under `workspace/` in the
    repeat's artifact folder: two files of the same name in two folders
    stay two files, and none of them overwrites the harness's own
    `answer.md` or `judge-prompt.md`.
    """
    paths: list[str] = []
    parts: list[str] = []
    for file in rt.collect(globs) if globs else []:
        rel = file.relative_to(rt.workspace).as_posix()
        text = file.read_text(encoding="utf-8", errors="replace")
        copy = art_dir / "workspace" / rel
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(text, encoding="utf-8")
        paths.append(f"artifacts/{index}/workspace/{rel}")
        parts.append(f"### File: {rel}\n\n{text}")
    return paths, parts


def describe_subject(scn: S.Scenario, argv: list[str]) -> str:
    """The sentence the judge reads about what made the artifact."""
    if scn.kind == "skill":
        return f"The `{scn.subject.skill}` skill of the guideline answered this prompt:\n\n{scn.subject.prompt}"
    if scn.kind == "command":
        return "This command ran:\n\n" + " ".join(argv)
    return f"A model answered this prompt directly:\n\n{scn.subject.prompt}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark/run.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", nargs="?", default="run", choices=["run", "list"], help="run a scenario, or list what there is"
    )
    parser.add_argument("--scenario", help="scenario name or path")
    parser.add_argument("--providers", default=None, help="bit flag (3, 7, 15) or names (anthropic,openai)")
    parser.add_argument("--effort", default=None, choices=list(J.EFFORTS), help="judge effort")
    parser.add_argument(
        "--repeat", type=int, default=3, help="how many times the subject runs; one run is an anecdote, so 3 by default"
    )
    parser.add_argument("--runtime", default="host", choices=list(RT.NAMES), help="where the subject runs")
    parser.add_argument("--runtime-config", default=None, help="JSON or YAML file with the runtime's settings")
    parser.add_argument("--target", default=None, help="a checkout the subject works on")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="folder the run folders are written under")
    parser.add_argument(
        "--claude", default=os.environ.get("CLAUDE_BIN", "claude"), help="the Claude Code binary the subject runs"
    )
    parser.add_argument(
        "--subject-model", default=None, help="the model the subject runs on; the scenario's, else the matrix's first"
    )
    parser.add_argument("--dry-run", action="store_true", help="resolve everything, write run.json, call nothing")
    parser.add_argument("--strict", action="store_true", help="a provider without a key fails the run")
    parser.add_argument("--build", action="store_true", help="build the container image before running")
    parser.add_argument(
        "--screencast-port", type=int, default=None, help="capture frames from a Chrome already listening on this port"
    )
    parser.add_argument("--screencast-seconds", type=float, default=10.0, help="how long to capture frames")
    return parser


def command_list(out: Path) -> int:
    print("scenarios:")
    for path in S.catalog(SCENARIOS):
        try:
            scn = S.load(path)
            print(f"  {scn.name:18} kind={scn.kind:8} judges={scn.judges.providers} effort={scn.judges.effort}")
        except S.ScenarioError as exc:
            print(f"  {path.stem:18} unreadable: {exc}")
    print("\nproviders:")
    for name, ready, keys in P.availability():
        flag = int(P.Provider[name.upper()])
        print(f"  {name:10} flag={flag:<3} key={'present' if ready else 'absent '} ({keys})")
    print(f"\nruns folder: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Path(args.out).resolve()
    if args.command == "list":
        return command_list(out)
    if not args.scenario:
        print("--scenario is required; `run.py list` shows the scenarios", file=sys.stderr)
        return 2

    try:
        scn = S.load(S.find(args.scenario, SCENARIOS))
        flags = P.parse(args.providers if args.providers is not None else scn.judges.providers)
        if scn.kind == "qa":
            context_text(scn)  # a missing context file stops the run before anything is spent
    except (S.ScenarioError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    effort = args.effort or scn.judges.effort
    matrix = J.load_matrix(MODELS)
    own_target = scn.resolve(scn.subject.target)
    target = Path(args.target).resolve() if args.target else own_target
    if target is not None and not target.is_dir():
        print(f"the target {target} is not a folder", file=sys.stderr)
        return 2

    run_id, run_dir = new_run_dir(out, scn.name)

    config: dict = {}
    if args.runtime_config:
        path = Path(args.runtime_config)
        config = S.parse_text(path.read_text(encoding="utf-8"), path.suffix) or {}
    if args.runtime == "container":
        config.setdefault("keys", [n for n in subject_keys(scn) if os.environ.get(RT.SUBJECT_KEYS[n])])
    # The subject lives outside the checkout, with copies of the plugin
    # payload and of the target, so neither an answer key nor the
    # repository's CLAUDE.md is in its reach.
    rt = RT.build(args.runtime, run_dir, target, config, plugin=ROOT if scn.kind != "qa" else None, sandbox=RT.new_sandbox())
    try:
        return execute(args, scn, rt, run_dir, run_id, target, own_target, config, flags, effort, matrix)
    finally:
        rt.teardown()


def execute(args, scn, rt, run_dir, run_id, target, own_target, config, flags, effort, matrix) -> int:
    """Everything after the runtime exists: the caller tears the runtime down whatever happens here."""
    rt.stage()
    model = subject_model(scn, args.subject_model, matrix)
    try:
        argv_subject = subject_argv(scn, plugin_name(ROOT), rt.plugin_path(), rt.target_path(), args.claude, model)
    except (S.ScenarioError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    # The evidence the judges get. The source is read from the target as
    # the runtime staged it, the copy the subject reads, so the judges and
    # the subject see the same files. The expected findings describe the
    # scenario's own target; on any other target they would be wrong, so
    # they are dropped and the run says so. The source goes either way.
    source_text = E.source(rt.target, scn.evidence.files) if rt.target and scn.evidence.files else ""
    expected_path = scn.resolve(scn.evidence.expected)
    expected_note = None
    if expected_path and target != own_target:
        expected_note = f"expected findings dropped: they describe {own_target}, and the run is on {target}"
        expected_path = None
    expected_text: str | None = None
    expected_data = None
    if expected_path:
        expected_text = expected_path.read_text(encoding="utf-8")
        expected_data = S.parse_text(expected_text, expected_path.suffix)
    evidence_text = E.render(expected_text, source_text)
    resolved = {
        "run_id": run_id,
        "scenario": scn.as_dict(),
        "runtime": {"name": rt.name, "config": config, "target": str(target) if target else None},
        "providers": {"flags": int(flags), "names": [P.name(p) for p in P.members(flags)]},
        "effort": effort,
        "repeat": args.repeat,
        "models": {P.name(p): J.models_for(matrix, P.name(p)) for p in P.members(flags)},
        "subject_argv": argv_subject,
        "subject_model": model,
        "guideline_sha": git_sha(ROOT),
        "target_sha": git_sha(target) if target else None,
        "evidence": {
            "files": list(scn.evidence.files),
            "source_chars": len(source_text),
            "expected": str(expected_path) if expected_path else None,
            "note": expected_note,
        },
        "started_at": R.now(),
    }
    (run_dir / "run.json").write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
    print(f"run folder: {run_dir}")
    if expected_note:
        print(expected_note)

    if args.dry_run:
        print(json.dumps(resolved, indent=2))
        print("dry run: nothing was executed and no provider was called")
        return 0

    missing = [P.name(p) for p in P.members(flags) if not P.available(p)]
    if missing and args.strict:
        print(f"strict: no key for {', '.join(missing)}", file=sys.stderr)
        return 3
    no_subject_key = [RT.SUBJECT_KEYS[n] for n in subject_keys(scn) if not os.environ.get(RT.SUBJECT_KEYS[n])]
    if scn.kind == "skill" and no_subject_key and args.strict:
        print(f"strict: the subject has no key of its own; set {', '.join(no_subject_key)}", file=sys.stderr)
        return 3

    if isinstance(rt, RT.ContainerRuntime) and args.build:
        with CliStream(run_dir / "streams" / "build.jsonl") as build_stream:
            status = rt.build(build_stream)
        if status.code != 0:
            print(f"the image build failed with {status.code}; see streams/build.jsonl", file=sys.stderr)
            return 4

    notes: list[str] = [expected_note] if expected_note else []
    screencast = None
    if args.screencast_port:
        from harness.capture import CdpScreencast

        screencast = CdpScreencast(FrameSink(run_dir / "streams" / "browser"), port=args.screencast_port)
        screencast.start(seconds=args.screencast_seconds)

    run = R.RunResult(
        run_id=run_id,
        scenario=scn.name,
        runtime=rt.name,
        started_at=resolved["started_at"],
        guideline_sha=resolved["guideline_sha"],
        target_sha=resolved["target_sha"],
        subject={
            "kind": scn.kind,
            "skill": scn.subject.skill,
            "prompt": scn.subject.prompt,
            "argv": argv_subject,
            "model": model,
            "provider": scn.subject.provider,
            "target": str(target) if target else None,
            "plugin": rt.plugin_path(),
            "max_turns": scn.subject.max_turns,
            "allowed_tools": list(scn.subject.allowed_tools),
        },
    )

    env = subject_env(scn)

    streams = CliStream(run_dir / "streams" / "cli.jsonl")
    failed_subjects: list[int] = []
    try:
        for index in range(max(1, args.repeat)):
            mark = streams.count
            streams.note(f"[repeat {index}] start")
            rt.prepare_repeat(index)  # every repeat starts in an empty workspace of its own
            if scn.kind == "qa":
                status, artifact = run_subject_qa(scn, streams, dict(os.environ), matrix, effort, model or "")
                models = [model] if model else []
            else:
                status = rt.run(argv_subject, rt.workspace, env, streams, timeout_s=scn.subject.timeout_s)
                lines = [r["line"] for r in CliStream.read(run_dir / "streams" / "cli.jsonl")[mark:] if r.get("s") == "out"]
                artifact, models, is_error = read_envelope("\n".join(lines))
                if is_error:
                    status = dataclasses.replace(status, is_error=True)
                if model and models and not any(m.startswith(model) for m in models):
                    notes.append(
                        f"repeat {index}: the subject was pinned to {model}, and the envelope reports {', '.join(models)}"
                    )
            streams.note(f"[repeat {index}] exit {status.code}")

            art_dir = run_dir / "artifacts" / str(index)
            art_dir.mkdir(parents=True, exist_ok=True)
            paths = []
            if scn.artifact.stdout:
                (art_dir / "answer.md").write_text(artifact + "\n", encoding="utf-8")
                paths.append(f"artifacts/{index}/answer.md")
            parts = [artifact] if scn.artifact.stdout else []
            file_paths, file_parts = collect_files(rt, scn.artifact.files, art_dir, index)
            paths += file_paths
            parts += file_parts
            blob = "\n\n".join(p for p in parts if p.strip()) or "(the subject produced nothing)"

            if not status.ok:
                # A subject that failed or ran out of time produced no answer
                # worth a judge's money, and a score of it would be a score of
                # the failure. The repeat is recorded with no judgement.
                failed_subjects.append(index)
                reason = "timed out" if status.timed_out else "is_error" if status.is_error else f"exit {status.code}"
                print(f"  repeat {index} subject failed ({reason}); not judged")
                run.repeats.append(
                    R.RepeatResult(index=index, exit_status=status.as_dict(), artifact_paths=paths, subject_models=models)
                )
                continue

            prompt = J.build_prompt(scn.rubric, describe_subject(scn, argv_subject), blob, evidence=evidence_text)
            expected = E.named(expected_data, blob)
            if expected is not None:
                print(f"  repeat {index} names {len(expected['named'])} of {expected['expected']} planted findings")
            (art_dir / "judge-prompt.md").write_text(prompt, encoding="utf-8")
            judgements = J.judge_all(flags, prompt, effort, matrix)
            for j in judgements:
                record = j.as_dict()
                record["raw"] = j.raw
                (run_dir / "judgements").mkdir(parents=True, exist_ok=True)
                (run_dir / "judgements" / f"{index}-{j.provider}.json").write_text(
                    json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                score = j.verdict.score if j.verdict else "-"
                print(f"  repeat {index} {j.provider:10} {j.model:24} {j.status:8} score {score}")
            run.repeats.append(
                R.RepeatResult(
                    index=index,
                    exit_status=status.as_dict(),
                    artifact_paths=paths,
                    judgements=judgements,
                    expected=expected,
                    subject_models=models,
                )
            )
    finally:
        streams.close()
        if screencast is not None:
            result = screencast.stop()
            notes.append(f"screencast: {result.frames} frame(s)" + (f", {result.error}" if result.error else ""))

    if failed_subjects:
        notes.append(f"subject failed in repeat(s) {', '.join(map(str, failed_subjects))}; not judged")
    run.notes = notes
    data = R.write_results(run, run_dir / "results.json")
    problems = R.validate(data, SCHEMA)
    if problems == [R.UNVALIDATED]:  # no validator here: say so, and claim nothing
        print(R.UNVALIDATED, file=sys.stderr)
        problems = []
    if problems:
        print("results.json does not match the schema:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
    R.write_report(run, run_dir / "report.md")

    summary = data["summary"]
    for provider, stats in summary["per_provider"].items():
        print(f"{provider:10} mean {stats['mean']} over {stats['n']} judgement(s), stdev {stats['stdev']}")
    if summary["self_judged"]:
        print(f"note: {summary['self_judged']}")
    for fallback in summary["fallbacks"]:
        print(f"{fallback['provider']:10} {fallback['to']} answered in place of {fallback['from']} {fallback['count']} time(s)")

    for skipped in summary["skipped"]:
        print(f"{skipped['provider']:10} not answered: {skipped['reason']}")
    print(f"report: {run_dir / 'report.md'}")
    if problems:
        return 5
    if failed_subjects:
        print(f"the subject failed in {len(failed_subjects)} of {len(run.repeats)} repeat(s)", file=sys.stderr)
        return 6
    if args.strict and summary["skipped"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
