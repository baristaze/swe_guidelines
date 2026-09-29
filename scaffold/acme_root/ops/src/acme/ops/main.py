"""`acme-ops`: traffic, stress, signals check, size, token (and the list
and the revoke of one's own), and work requeue, each against one named
environment, and workos-bootstrap against one WorkOS environment. Exit 0
when the run did what was asked, 1 when a stress target was missed, a reader
found nothing, a redirect needs the WorkOS dashboard, or the operator plane
refused a requeue, 2 for a bad invocation, a credential the operator plane
refused, or a WorkOS key that is not the application's."""

import argparse
import asyncio
import getpass
import json
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import aioboto3
import httpx

from acme.client.client import ApiClient, ApiError
from acme.client.schema import OrgPageView, UserPageView
from acme.infra.aws_clients import client_config
from acme.ops.environments import (
    CLOUD_ENVIRONMENTS,
    LOCAL_OPERATORS,
    Environment,
    load_environment,
    local_addresses,
    ops_file,
    repository_root,
    write_value,
)
from acme.ops.profiles import PROFILES, profile_named
from acme.ops.report import Report
from acme.ops.signals import Readback, SignalsInterface
from acme.ops.signals.cloud import SessionLike, SignalsCloudImpl
from acme.ops.signals.local import SignalsLocalImpl
from acme.ops.stress import (
    READBACK_WAIT_SECONDS,
    load_scenario,
    read_back,
    verdict,
    window_start,
    with_duration,
    with_target,
)
from acme.ops.traffic import (
    NO_ONE_SIGNED_IN,
    OPERATOR_APP,
    TokenRefused,
    app_version,
    is_run_tenant,
    run_traffic,
)
from acme.ops.workos import workos_bootstrap_command

OK, FAILED, USAGE = 0, 1, 2


def file_lines(path: Path | None) -> Callable[[], Iterable[str]]:
    def read() -> Iterable[str]:
        return path.read_text().splitlines() if path and path.is_file() else []

    return read


def signals_for(env: Environment, *, log_file: Path | None = None) -> SignalsInterface:
    """The reader an environment gets: the cloud impl for the deployed names,
    the local impl for everything else. A deployed environment reads its logs,
    metrics, and traces out of its own account and needs no error tracker; one
    that names none reports the error event as not read. Locally the tracker
    is part of the stack, so an environment missing it is a broken stack and
    is refused. Either way the tracker project is the product's one project,
    and the reader is handed the environment, which is what separates the
    events inside it."""
    if env.is_cloud:
        return SignalsCloudImpl(
            environment=env.name,
            profile=env.aws_profile,
            region=env.aws_region,
            sentry_url=env.error_tracker_url,
            sentry_token=env.error_tracker_token,
            sentry_org=env.error_tracker_org,
            sentry_project=env.error_tracker_project,
        )
    if not (env.prometheus_url and env.jaeger_url and env.error_tracker_url):
        raise ValueError(f"environment {env.name!r} names no Prometheus, Jaeger, and error tracker")
    return SignalsLocalImpl(
        prometheus_url=env.prometheus_url,
        jaeger_url=env.jaeger_url,
        error_tracker_url=env.error_tracker_url,
        error_tracker_token=env.error_tracker_token or "",
        error_tracker_org=env.error_tracker_org,
        error_tracker_project=env.error_tracker_project,
        environment=env.name,
        logs=file_lines(log_file),
    )


async def check_signals(
    signals: SignalsInterface, request_id: str, since: datetime, metric: str
) -> Readback:
    return Readback(
        request_id=request_id,
        log_lines=await signals.log_lines(request_id),
        metric_delta=await signals.metric_delta(metric, {}, since),
        trace=await signals.trace(request_id),
        error_event=await signals.error_event(request_id),
        error_events_read=signals.reads_error_events,
    )


def readback_text(readback: Readback, metric: str) -> str:
    lines = [f"request {readback.request_id}"]
    lines.append(
        f"  log lines: {len(readback.log_lines)} found"
        + (f"; first: {readback.log_lines[0][:120]}" if readback.log_lines else "")
    )
    lines.append(
        f"  metric {metric}: "
        + ("no series" if readback.metric_delta is None else f"moved by {readback.metric_delta:g}")
    )
    lines.append(
        "  trace: "
        + (
            f"{readback.trace.trace_id} ({len(readback.trace.span_names)} spans)"
            if readback.trace
            else "not found"
        )
    )
    if readback.error_event:
        event = (
            f"{readback.error_event.event_id} in issue {readback.error_event.issue_id} "
            f"({readback.error_event.title})"
        )
    elif not readback.error_events_read:
        event = "not read, the environment names no error tracker"
    else:
        event = "not found"
    lines.append(f"  error event: {event}")
    return "\n".join(lines) + "\n"


# Commands


def traffic_exit_code(report: Report) -> int:
    """What a traffic run exits with: 0 when it drove a session through, 1
    when it did not. A run that started no session at all drove nothing for a
    reason of its own, and an exit status alone reads like any other failure,
    so it says the reason on stderr too."""
    if report.sessions.started == 0:
        print(NO_ONE_SIGNED_IN, file=sys.stderr)
        return FAILED
    return OK if report.sessions.completed > 0 else FAILED


async def traffic_command(args: argparse.Namespace) -> tuple[int, Report]:
    env = load_environment(args.env)
    profile = profile_named(args.profile)
    result = await run_traffic(
        env, profile, duration_seconds=args.duration, orgs=args.orgs, ramp_seconds=args.ramp
    )
    sys.stdout.write(result.report.table())
    return traffic_exit_code(result.report), result.report


async def stress_command(args: argparse.Namespace) -> tuple[int, Report]:
    env = load_environment(args.env)
    scenario = load_scenario(Path(args.scenario))
    if args.duration is not None:
        scenario = with_duration(scenario, args.duration)
    scenario = with_target(scenario, p95_ms=args.p95_ms, error_ratio=args.error_ratio)
    result = await run_traffic(
        env,
        scenario.profile,
        duration_seconds=scenario.duration_seconds,
        orgs=args.orgs,
        ramp_seconds=scenario.ramp_seconds,
    )
    report = result.report
    sys.stdout.write(report.table())
    signals = signals_for(env)
    since = window_start(report)
    # The scrape lags the run; the readback waits for it to catch up.
    deadline = datetime.now(UTC) + timedelta(seconds=READBACK_WAIT_SECONDS)
    readback = await read_back(signals, since)
    while (readback.requests or 0) < report.requests and datetime.now(UTC) < deadline:
        await asyncio.sleep(5)
        readback = await read_back(signals, since)
    outcome = verdict(scenario, report, readback)
    sys.stdout.write(outcome.text(scenario, report, readback))
    return (OK if outcome.passed else FAILED), report


def write_report(args: argparse.Namespace, outcome: tuple[int, Report]) -> int:
    """The JSON file a `--report` asked for, written once the loop is done."""
    code, report = outcome
    if args.report:
        Path(args.report).write_text(report.to_json())
        print(f"wrote {args.report}")
    return code


async def signals_command(args: argparse.Namespace) -> int:
    env = load_environment(args.env)
    signals = signals_for(env, log_file=Path(args.log_file) if args.log_file else None)
    print(signals.describe())
    since = datetime.now(UTC) - timedelta(minutes=args.since_minutes)
    readback = await check_signals(signals, args.request_id, since, args.metric)
    sys.stdout.write(readback_text(readback, args.metric))
    found = bool(readback.log_lines or readback.trace or readback.error_event)
    return OK if found else FAILED


async def run_tenants(client: ApiClient) -> tuple[int, int]:
    """The tenants traffic runs created that are still live, and their
    people: every page of the orgs, the ones named for a run, and every page
    of each one's members."""
    orgs = users = 0
    cursor: str | None = None
    while True:
        params: dict[str, object] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = OrgPageView.model_validate(
            await client.request("GET", "/v1/admin/orgs", params=params)
        )
        for org in page.items:
            if not is_run_tenant(org.slug) or org.deleted_at is not None:
                continue
            orgs += 1
            members: str | None = None
            while True:
                people: UserPageView = await client.admin_members(org.id, cursor=members)
                users += len(people.items)
                members = people.next_cursor
                if not members:
                    break
        cursor = page.next_cursor
        if not cursor:
            return orgs, users


async def size_command(
    args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    """The platform's size as the first responder reads it before an
    escalation: the tenants and users the traffic generator created for its
    runs are left out, since they are the team's own traffic. The size is the
    maintenance worker's latest count, so how long ago it counted is printed
    beside it; the runs' tenants are read now."""
    env = load_environment(args.env)
    if not env.operator_token:
        print(
            f"environment {env.name!r} holds no operator token; write one with "
            f"`uv run acme-ops token --env {env.name} --identity operator`",
            file=sys.stderr,
        )
        return USAGE
    async with ApiClient(
        env.api_url,
        app=OPERATOR_APP,
        app_version=app_version(),
        token=env.operator_token,
        transport=transport,
    ) as client:
        try:
            size = await client.admin_size()
            run_orgs, run_users = await run_tenants(client)
        except ApiError as error:
            if error.status == 401:
                raise TokenRefused(env, "operator") from None
            raise
    values = size.model_dump()
    values["tenants"] = max(size.tenants - run_orgs, 0)
    values["users"] = max(size.users - run_users, 0)
    for key, value in values.items():
        print(f"{key:<24} {value}")
    age = max(int((datetime.now(UTC) - size.counted_at).total_seconds()), 0)
    print(f"{'counted':<24} {age // 60} min {age % 60} s ago, by the maintenance worker's sweep")
    print(
        f"{'traffic run tenants':<24} {run_orgs} left out ({run_users} users); "
        "their events stay in the day's counts"
    )
    return OK


def sso_profile_of(env_name: str) -> str:
    """The person's own Identity Center profile for the environment, from
    deployment/cloud/environments.json."""
    root = repository_root()
    if root is None:
        raise ValueError(
            "run this from the acme checkout; it reads deployment/cloud/environments.json"
        )
    layout = json.loads((root / "deployment" / "cloud" / "environments.json").read_text())
    return str(layout["environments"][env_name]["sso_profile"])


async def read_provisioner_token(env: Environment, profile: str | None = None) -> str:
    """The token the grant job wrote into `acme-<env>-provisioner-token`,
    read under the person's own sign-in: the Identity Center profile by
    default, or the one named (production's sign-in reads nothing secret,
    so there it is the power or the administrator profile). Never an
    investigate profile: the investigate role is denied every secret value,
    and an agent never holds this token's source."""
    chosen = profile or sso_profile_of(env.name)
    if chosen.endswith("-investigate"):
        raise ValueError(
            f"{chosen} is an agent's read-only profile and reads no secret; "
            "copy the provisioner's token under your own sign-in (--profile)"
        )
    session = cast(
        SessionLike,
        aioboto3.Session(profile_name=chosen, region_name=env.aws_region),
    )
    async with session.client(
        "secretsmanager", config=client_config(timedelta(seconds=10))
    ) as secrets:
        answer = await secrets.get_secret_value(SecretId=f"acme-{env.name}-provisioner-token")
    token = str(answer.get("SecretString") or "")
    if not token:
        raise ValueError(
            f"acme-{env.name}-provisioner-token holds no token yet; dispatch grant-operator.yml "
            "with mint_token: provisioner first"
        )
    return token


DEVICE_SLOW_DOWN_SECONDS = 5.0
"""How much longer each ask waits once the API answers `sign_in_slow_down`."""


async def device_sign_in(
    client: ApiClient,
    *,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """A person's sign-in through the identity provider, for a terminal with
    no browser of its own: the code and the address go to stderr, the person
    confirms them in any browser, and the answer is the sign-in credential.
    Asked again every interval the start named, longer after each
    `sign_in_slow_down`, until the device code expires."""
    started = await client.start_device_sign_in()
    print(
        f"open {started.verification_uri_complete} and confirm the code {started.user_code}",
        file=sys.stderr,
    )
    interval = float(started.interval)
    deadline = clock() + started.expires_in
    while clock() < deadline:
        await sleep(interval)
        try:
            login = await client.finish_device_sign_in(started.device_code)
        except ApiError as error:
            if error.code == "sign_in_slow_down":
                interval += DEVICE_SLOW_DOWN_SECONDS
                continue
            if error.code == "sign_in_pending":
                continue
            raise
        return login.token
    raise ValueError("the sign-in was not confirmed before its code expired; run the command again")


@dataclass(frozen=True)
class MintedToken:
    """An operator token in the clear, and the id that names it in the list
    and the revoke, which is no secret."""

    token: str
    id: UUID


async def sign_out(client: ApiClient, token: str) -> None:
    """Ends the credential given, best effort: a sign-in or a token nobody
    needs any more. One the plane cannot reach now ends at its own expiry."""
    try:
        await client.request("POST", "/v1/auth/logout", token=token)
    except ApiError, httpx.HTTPError:
        print(
            "acme-ops: the sign-out did not reach the API; it ends at its expiry",
            file=sys.stderr,
        )


async def mint_operator_token(
    env: Environment,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    dev_email: str | None = None,
    sleep: Callable[[float], Awaitable[object]] | None = None,
    permission: str = "read",
) -> MintedToken:
    """A person's operator token, `read` unless asked otherwise. The person
    signs in through the identity provider (or, on the local stack with
    `--dev-email`, by the local sign-in), then the TOTP code is asked for
    here, in the person's own terminal, and goes only to the second-factor
    route, which ends the sign-in it verified. The mint ends the verified
    one in turn, so what is left is the token. A mint the plane refuses ends
    nothing, so that sign-in is signed out here before the refusal goes on."""
    async with ApiClient(
        env.api_url, app=OPERATOR_APP, app_version=app_version(), transport=transport
    ) as client:
        if dev_email is not None:
            login_token = (await client.dev_sign_in(dev_email)).token
        else:
            login_token = await device_sign_in(client, sleep=sleep or asyncio.sleep)
        code = (await asyncio.to_thread(getpass.getpass, "TOTP code: ")).strip()
        verified = await client.verify_second_factor(login_token, code)
        try:
            minted = await client.request(
                "POST", "/v1/admin/me/tokens", json={"permission": permission}, token=verified.token
            )
        except ApiError:
            await sign_out(client, verified.token)
            raise
    return MintedToken(token=str(minted["token"]), id=UUID(str(minted["id"])))


def in_a_persons_terminal(args: argparse.Namespace, env: Environment) -> bool:
    """Whether a sign-in may start here: a terminal a person holds, and the
    local sign-in only where the local stack serves it. Says why not."""
    if not sys.stdin.isatty():
        print(
            "an operator's sign-in happens in a person's own terminal: the person signs "
            "in and gives the TOTP code there, and no agent holds either",
            file=sys.stderr,
        )
        return False
    if getattr(args, "dev_email", None) is not None and env.name != "local":
        print(
            "--dev-email signs in by the local sign-in, which only --env local serves",
            file=sys.stderr,
        )
        return False
    return True


TOKEN_KEYS = {"operator": "ACME_OPERATOR_TOKEN", "provisioner": "ACME_PROVISIONER_TOKEN"}


def mint_local_token(identity: str) -> str:
    """The token of one of the two local operators `make seed` puts on the
    allowlist, minted by the grant command on the local database, which is
    the one place it prints a token. Captured here and never echoed."""
    root = repository_root()
    if root is None:
        raise ValueError("run this from the acme checkout; it mints through acme-api")
    answer = subprocess.run(
        [
            "uv",
            "run",
            "--package",
            "acme-api",
            "acme-api",
            "grant-operator",
            "--mint-token",
            identity,
            "--email",
            LOCAL_OPERATORS[identity],
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    minted = [line for line in answer.stdout.splitlines() if line.startswith("opr_")]
    if answer.returncode != 0 or len(minted) != 1:
        said = (answer.stderr.strip().splitlines() or ["no answer"])[-1]
        raise ValueError(
            f"the local {identity}'s token was not minted ({said}); `make seed` puts "
            f"{LOCAL_OPERATORS[identity]} on the local allowlist"
        )
    return minted[0]


def write_local_token(env: Environment, identity: str, token: str) -> Path:
    """The token into `local.env`, which is made owner-only, naming the local
    stack's addresses, when it is missing; every other line is kept."""
    file = ops_file(env.name)
    if not file.exists():
        for key, value in local_addresses(env).items():
            write_value(file, key, value)
    write_value(file, TOKEN_KEYS[identity], token)
    return file


async def token_command(
    args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    """Writes an operator token into the environment's file without printing
    it: the operator's, minted after a sign-in with the second factor, or the
    provisioner's, copied from the secret the grant job wrote. On the local
    stack either is the local operator's that `make seed` made, minted by the
    grant command, unless `--dev-email` names a person who signs in. With
    `--list` or `--revoke`, reads or ends the operator's own tokens instead."""
    if getattr(args, "list", False) or getattr(args, "revoke", None) is not None:
        return await own_tokens_command(args, transport)
    env = load_environment(args.env)
    file = ops_file(env.name)
    if env.name not in CLOUD_ENVIRONMENTS and getattr(args, "dev_email", None) is None:
        file = write_local_token(env, args.identity, mint_local_token(args.identity))
        print(f"wrote {TOKEN_KEYS[args.identity]} into {file}; it expires within the hour")
        return OK
    if args.identity == "operator":
        if not in_a_persons_terminal(args, env):
            return USAGE
        minted = await mint_operator_token(
            env, transport, dev_email=getattr(args, "dev_email", None)
        )
        write_value(file, "ACME_OPERATOR_TOKEN", minted.token)
        print(
            f"wrote ACME_OPERATOR_TOKEN into {file}; it expires within the hour. "
            f"Its id is {minted.id}: `--revoke {minted.id}` ends it sooner"
        )
        return OK
    if env.name not in CLOUD_ENVIRONMENTS:
        print("--dev-email signs in a person, who mints an operator's token", file=sys.stderr)
        return USAGE
    write_value(file, "ACME_PROVISIONER_TOKEN", await read_provisioner_token(env, args.profile))
    print(f"wrote ACME_PROVISIONER_TOKEN into {file}; it expires within the hour")
    return OK


async def own_tokens_command(
    args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    """The operator's own live tokens, or the end of one by its id, under the
    env file's operator token. A token ends at once; its next request is
    refused. Another operator's token is not the operator's to end: the grant
    job's disable is (docs/runbooks/operator.md)."""
    env = load_environment(args.env)
    if not env.operator_token:
        print(
            f"environment {env.name!r} holds no operator token; write one with "
            f"`uv run acme-ops token --env {env.name} --identity operator`",
            file=sys.stderr,
        )
        return USAGE
    async with ApiClient(
        env.api_url,
        app=OPERATOR_APP,
        app_version=app_version(),
        token=env.operator_token,
        transport=transport,
    ) as client:
        try:
            if args.revoke is not None:
                ended = await client.admin_revoke_token(args.revoke)
                print(f"revoked {ended.id} ({ended.permission.value}) at {ended.revoked_at}")
                return OK
            cursor: str | None = None
            while True:
                page = await client.admin_tokens(cursor=cursor)
                for token in page.items:
                    print(
                        f"{token.id}  {token.permission.value:<5}  created {token.created_at}  "
                        f"expires {token.expires_at}"
                    )
                cursor = page.next_cursor
                if not cursor:
                    return OK
        except ApiError as error:
            if error.status == 401:
                raise TokenRefused(env, "operator") from None
            if error.status == 404:
                print(
                    f"acme-ops: no live token of yours has the id {args.revoke}",
                    file=sys.stderr,
                )
                return FAILED
            raise


async def work_requeue_command(
    args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    """Sends one failed work item back to the queue. A write, so it is a
    person's one named step: the person signs in with the second factor, and
    a `write` token is minted for this call, used once, and kept nowhere.
    The env file keeps the `read` token it had, and an agent that holds it
    cannot run this. The `write` token is signed out once the call is made,
    so it ends then and not an hour later."""
    env = load_environment(args.env)
    if not in_a_persons_terminal(args, env):
        return USAGE
    try:
        minted = await mint_operator_token(
            env, transport, dev_email=getattr(args, "dev_email", None), permission="write"
        )
    except ApiError as error:
        if error.status == 403:
            print(
                "your operator entry does not carry write; a requeue needs the write "
                "permission (docs/runbooks/operator.md, The grant)",
                file=sys.stderr,
            )
            return FAILED
        raise
    async with ApiClient(
        env.api_url,
        app=OPERATOR_APP,
        app_version=app_version(),
        token=minted.token,
        transport=transport,
    ) as client:
        try:
            item = await client.admin_requeue_work(args.org, args.item)
        except ApiError as error:
            print(f"acme-ops: not requeued: {error}", file=sys.stderr)
            return FAILED
        finally:
            await sign_out(client, minted.token)
    print(
        f"requeued {item.id} ({item.kind.value}) in org {args.org}: {item.status.value}, "
        f"{item.attempts} of {item.max_attempts} attempts spent, available now"
    )
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acme-ops")
    sub = parser.add_subparsers(dest="command", required=True)

    p_traffic = sub.add_parser("traffic", help="drive realistic sessions at a profile")
    p_traffic.add_argument("--env", required=True, help="local, staging, or production")
    p_traffic.add_argument("--profile", default="light", choices=list(PROFILES))
    p_traffic.add_argument("--duration", type=float, help="seconds; the profile's when absent")
    p_traffic.add_argument(
        "--orgs",
        type=int,
        help="tenants to provision through the operator plane; 0 drives the seeded org",
    )
    p_traffic.add_argument("--ramp", type=float, default=0.0, help="seconds to reach concurrency")
    p_traffic.add_argument("--report", help="write the report as JSON here")

    p_stress = sub.add_parser("stress", help="run a scenario and judge it against its target")
    p_stress.add_argument("--scenario", required=True, help="ops/stress/<name>.yaml")
    p_stress.add_argument("--env", default="local")
    p_stress.add_argument("--orgs", type=int)
    p_stress.add_argument(
        "--duration",
        type=float,
        help="seconds; the scenario's when absent. The target does not move with it.",
    )
    p_stress.add_argument(
        "--p95-ms",
        type=float,
        help="the pass mark for the working requests' p95, in milliseconds; "
        "the scenario's when absent",
    )
    p_stress.add_argument(
        "--error-ratio",
        type=float,
        help="the share of requests that may fail, as a fraction; the scenario's when absent",
    )
    p_stress.add_argument("--report")

    p_signals = sub.add_parser("signals", help="read the signals back")
    signals_sub = p_signals.add_subparsers(dest="signals_command", required=True)
    p_check = signals_sub.add_parser("check", help="what each reader finds for one request id")
    p_check.add_argument("--env", required=True)
    p_check.add_argument("--request-id", required=True)
    p_check.add_argument("--since-minutes", type=int, default=60)
    p_check.add_argument("--metric", default="acme_http_requests_total")
    p_check.add_argument("--log-file", help="the process's captured log, for the local reader")

    p_size = sub.add_parser("size", help="the platform's size as the operator plane reports it")
    p_size.add_argument("--env", required=True)

    p_token = sub.add_parser(
        "token", help="write an operator token into the env file, or list or revoke your own"
    )
    p_token.add_argument("--env", required=True)
    p_token_action = p_token.add_mutually_exclusive_group(required=True)
    p_token_action.add_argument("--identity", choices=["operator", "provisioner"])
    p_token_action.add_argument(
        "--list",
        action="store_true",
        help="your own live operator tokens, under the env file's operator token",
    )
    p_token_action.add_argument(
        "--revoke",
        type=UUID,
        metavar="TOKEN_ID",
        help="end one of your own operator tokens now, by the id the mint or --list printed",
    )
    p_token.add_argument(
        "--profile",
        help="provisioner only: the person's own AWS profile that reads the secret; "
        "the environment's sign-in profile when absent (production's needs acme-prod-power)",
    )
    p_token.add_argument(
        "--dev-email",
        help="operator on --env local only: sign in by the local sign-in with this address, "
        "instead of minting the local read operator's token",
    )

    p_work = sub.add_parser("work", help="the work queue, as an operator moves it")
    work_sub = p_work.add_subparsers(dest="work_command", required=True)
    p_requeue = work_sub.add_parser(
        "requeue",
        help="send one failed work item back to the queue; a person's step, under a "
        "write token minted for it",
    )
    p_requeue.add_argument("--env", required=True)
    p_requeue.add_argument("--org", required=True, type=UUID, help="the org the item is in")
    p_requeue.add_argument("item", type=UUID, help="the failed item's id")
    p_requeue.add_argument(
        "--dev-email",
        help="on --env local only: sign in by the local sign-in with this address "
        "instead of through the identity provider",
    )

    p_workos = sub.add_parser(
        "workos-bootstrap",
        help="reconcile the WorkOS application with deployment/workos/environments.yaml",
    )
    p_workos.add_argument("--environment", required=True, help="staging or production")
    p_workos.add_argument(
        "--apply", action="store_true", help="make the changes; without it, only say them"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "traffic":
            return write_report(args, asyncio.run(traffic_command(args)))
        if args.command == "stress":
            return write_report(args, asyncio.run(stress_command(args)))
        if args.command == "signals":
            return asyncio.run(signals_command(args))
        if args.command == "token":
            return asyncio.run(token_command(args))
        if args.command == "work":
            return asyncio.run(work_requeue_command(args))
        if args.command == "workos-bootstrap":
            return asyncio.run(workos_bootstrap_command(args))
        return asyncio.run(size_command(args))
    except (ValueError, FileNotFoundError) as error:
        print(f"acme-ops: {error}", file=sys.stderr)
        return USAGE


if __name__ == "__main__":
    sys.exit(main())
