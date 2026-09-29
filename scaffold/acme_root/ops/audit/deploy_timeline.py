"""Where a deploy's minutes went, from what the pipeline and the cluster
already record.

    gh run view <run id> --json createdAt,updatedAt,jobs > run.json
    uv run python ops/audit/deploy_timeline.py steps run.json [--at-least 5]

    aws ecs describe-services --cluster acme-<env> --services api maintenance \\
      --profile acme-<env>-investigate > services.json
    uv run python ops/audit/deploy_timeline.py events services.json \
      --since <ISO time> --until <ISO time>

`steps` prints every job of the run with its id (for `gh run view --log
--job`), its start and end in UTC, its start as an offset from the run's
creation, and its length, then every step at least `--at-least` seconds
long, longest first, with its start and end, so the few steps that hold the
minutes stand out and a window for the cluster reads off them. `events`
prints the ECS service events inside the window, oldest first, in UTC and
as an offset from `--since`: a task started, registered
with its target group, failed its health checks, drained, and the service
reaching a steady state. Neither calls anything; each reads a file.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def when(value: str) -> datetime:
    """An ISO 8601 time, as UTC."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def clock(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}m{whole % 60:02d}s"


def utc(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%H:%M:%SZ")


def steps(run: dict[str, Any], at_least: float) -> list[str]:
    """The run's jobs in the order they started, then its long steps, every
    time in UTC, so a window for the cluster's events and logs reads off it."""
    start = when(run["createdAt"])
    jobs = [j for j in run["jobs"] if j.get("startedAt") and j.get("completedAt")]
    jobs.sort(key=lambda j: j["startedAt"])
    end = max((when(j["completedAt"]) for j in jobs), default=start)
    lines = [
        f"run: {clock((end - start).total_seconds())} from creation ({utc(start)}) "
        f"to its last job's end ({utc(end)})",
        "",
        "| Job | Job id | From | To | Starts at | Takes |",
        "|---|---|---|---|---|---|",
    ]
    for job in jobs:
        began, ended = when(job["startedAt"]), when(job["completedAt"])
        at, length = (began - start).total_seconds(), (ended - began).total_seconds()
        lines.append(
            f"| {job['name']} | {job.get('databaseId', '')} | {utc(began)} | {utc(ended)} "
            f"| +{clock(at)} | {clock(length)} |"
        )
    long: list[tuple[float, str, str, datetime, datetime]] = []
    for job in jobs:
        for step in job.get("steps", []):
            if not step.get("startedAt") or not step.get("completedAt"):
                continue
            began, ended = when(step["startedAt"]), when(step["completedAt"])
            length = (ended - began).total_seconds()
            if length >= at_least:
                long.append((length, job["name"], step["name"], began, ended))
    lines += [
        "",
        f"| Step (at least {clock(at_least)}) | Job | From | To | Takes |",
        "|---|---|---|---|---|",
    ]
    for length, job_name, step_name, began, ended in sorted(long, reverse=True):
        lines.append(
            f"| {step_name} | {job_name} | {utc(began)} | {utc(ended)} | {clock(length)} |"
        )
    return lines


def events(services: dict[str, Any], since: datetime, until: datetime) -> list[str]:
    """Every service event inside the window, oldest first, in UTC."""
    found: list[tuple[datetime, str, str]] = []
    for service in services.get("services", []):
        for event in service.get("events", []):
            at = when(event["createdAt"])
            if since <= at <= until:
                found.append((at, service["serviceName"], event["message"]))
    found.sort()
    lines = ["| At (UTC) | Offset | Service | Event |", "|---|---|---|---|"]
    for at, service_name, message in found:
        lines.append(
            f"| {utc(at)} | +{clock((at - since).total_seconds())} | {service_name} | {message} |"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deploy_timeline", description=(__doc__ or "").split("\n\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("steps", help="a run's jobs and its long steps, from gh run view --json")
    s.add_argument("file", type=Path)
    s.add_argument(
        "--at-least", type=float, default=5.0, help="seconds; shorter steps are left out"
    )
    e = sub.add_parser("events", help="the ECS service events of a window, from describe-services")
    e.add_argument("file", type=Path)
    e.add_argument("--since", required=True, help="ISO 8601, with its zone")
    e.add_argument("--until", required=True, help="ISO 8601, with its zone")
    args = parser.parse_args(argv)
    data = json.loads(args.file.read_text())
    if args.command == "steps":
        print("\n".join(steps(data, args.at_least)))
    else:
        print("\n".join(events(data, when(args.since), when(args.until))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
