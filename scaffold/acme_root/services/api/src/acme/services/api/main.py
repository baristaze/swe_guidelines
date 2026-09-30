"""The service binary is also its own operations CLI: serve, migrate,
bootstrap, add-member, grant-operator, and openapi are subcommands of one
entry point. Each one boots the same way before it does anything else, and
logs to standard error, so standard output carries only what a command
prints (the OpenAPI document, a local token)."""

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import uvicorn

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.impl.configured import absent_integrations
from acme.om.base import new_id
from acme.om.context import AppContext, AppType, OperatorRole, RequestContext, Role
from acme.om.exceptions import Conflict
from acme.om.storage import migrate
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer, boot, memory_storage, postgres_storage
from acme.services.api.gateway.observability import TargetRedactor
from acme.services.api.realtime.timeouts import (
    SERVER_PING_INTERVAL_SECONDS,
    SERVER_PING_TIMEOUT_SECONDS,
)
from acme.services.api.settings import ApiSettings
from acme.services.api.token_secrets import TOKEN_HOLDERS, put_token, token_secret_name

log = logging.getLogger(__name__)

LOCAL_TOKEN_HOLDER = "operator"
"""The local read operator, whose `read` token `make seed` and `acme-ops
token --env local` mint and print on a local database. It has no secret in a
cloud: there an operator's token is their own, minted after a sign-in with a
code."""


def server_options(settings: ApiSettings) -> dict[str, Any]:
    """What uvicorn is told beyond the address. Forwarded headers are honored
    only from the proxies settings name; with none named the peer is the
    client, so nothing outside the load balancer can choose its own address
    for the rate limit. uvicorn's access log is off: the observability
    middleware writes the line, by route template, so neither a caller's
    path nor the query string a socket ticket rides in is ever logged. The protocol ping on every
    socket is named here rather than left at uvicorn's default, because
    the interval and the timeout are pinned against the load balancer's
    idle timeout (`realtime/timeouts.py`)."""
    return {
        "proxy_headers": bool(settings.trusted_proxies),
        "forwarded_allow_ips": list(settings.trusted_proxies),
        "log_config": None,
        "access_log": False,
        "ws_ping_interval": SERVER_PING_INTERVAL_SECONDS,
        "ws_ping_timeout": SERVER_PING_TIMEOUT_SECONDS,
    }


def configure_server_logging() -> None:
    """uvicorn logs through the root handler boot configured (`log_config`
    is None); the one thing changed is that its lines lose the request's
    target, the path and the query string."""
    uvicorn_error = logging.getLogger("uvicorn.error")
    if not any(isinstance(f, TargetRedactor) for f in uvicorn_error.filters):
        uvicorn_error.addFilter(TargetRedactor())


def serve(args: argparse.Namespace) -> int:
    settings = ApiSettings()
    boot(settings)
    configure_server_logging()
    uvicorn.run(
        "acme.services.api.app:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        **server_options(settings),
    )
    return 0


def command_request(settings: ApiSettings) -> RequestContext:
    """The request stage an ops command mints once at its edge."""
    return RequestContext(
        request_id=new_id(), app=AppContext(type=AppType.CLI, version=f"cli@{settings.version}")
    )


def migrate_roles(args: argparse.Namespace) -> int:
    boot(ApiSettings())
    if args.action == "ensure-logins":
        # The migrate task's first step, the one command run as the master.
        return migrate.main(["ensure-logins"])
    forwarded = (
        ["upgrade"] + (["--all"] if args.all else []) + (["--role", args.role] if args.role else [])
    )
    return migrate.main(forwarded)


def bootstrap(args: argparse.Namespace) -> int:
    async def run() -> int:
        settings = ApiSettings()
        boot(settings)
        settings.refuse_remote()  # a development seed never reaches a shared database
        container = AppContainer.build(settings)
        await container.start()
        try:
            ctx, org = await container.managers.tenancy.bootstrap(
                command_request(settings),
                args.org,
                args.slug,
                args.email,
                args.name,
                operator_role=OperatorRole(args.operator_role) if args.operator else None,
            )
        except Conflict:
            # The only conflict bootstrap raises is a taken slug.
            if not args.if_absent:
                raise
            print(f"org {args.slug} already exists; nothing to do")
            return 0
        finally:
            await container.close()
        print(f"bootstrapped org {org.slug} ({org.id}) with owner {ctx.user_id}")
        return 0

    return asyncio.run(run())


def grant_operator(args: argparse.Namespace) -> int:
    """The grant job's command, run as a one-off task on the deployed image,
    and locally the same way. It puts an identity on the operator allowlist
    or disables its entry, or mints the operator token of the provisioner or
    the smoke identity. The task holds the database URLs and nothing else:
    no queue, bucket, or application secret. So its managers run over the
    database and over the local twins of the rest, which a grant reaches
    only to publish its audit row on an in-process bus nobody listens to."""

    async def run() -> int:
        settings = ApiSettings()
        boot(settings)
        with tempfile.TemporaryDirectory() as tmp:
            container = AppContainer.over(
                settings,
                postgres_storage(settings),
                InfraLocalImpl(Path(tmp)),
                absent_integrations(),
            )
            await container.start()
            try:
                return await granted(container, settings, args)
            finally:
                await container.close()

    return asyncio.run(run())


async def granted(container: AppContainer, settings: ApiSettings, args: argparse.Namespace) -> int:
    tenancy = container.managers.tenancy
    rctx = command_request(settings)
    if args.mint_token:
        # The smoke test and the local read operator read and never write,
        # whatever the entry grants; the provisioner's token carries the
        # entry's permission.
        holder: str = args.mint_token
        if holder == LOCAL_TOKEN_HOLDER and settings.is_cloud_environment:
            log.error("the %s token is minted on a local database only", holder)
            return 2
        role = OperatorRole.READ if holder in ("smoke", LOCAL_TOKEN_HOLDER) else None
        expires_in = None if args.expires_in is None else timedelta(seconds=args.expires_in)
        issued = await tenancy.grant_operator_token(rctx, args.email, expires_in, role)
        if settings.is_cloud_environment:
            # Handed over through the secret store and never printed.
            name = token_secret_name(settings.environment, holder)
            await put_token(settings, name, issued.token)
            log.info("wrote the %s token to %s, expiring %s", holder, name, issued.expires_at)
            return 0
        # A developer's own database is the one place a token is printed.
        settings.refuse_remote()
        print(issued.token)
        return 0
    if args.disable:
        identity = await tenancy.disable_operator(rctx, args.email)
        log.info("identity %s is off the operator allowlist", identity.id)
        return 0
    identity = await tenancy.grant_operator(rctx, args.email, OperatorRole(args.permission))
    log.info("identity %s is on the operator allowlist with %s", identity.id, args.permission)
    return 0


def add_member(args: argparse.Namespace) -> int:
    async def run() -> int:
        settings = ApiSettings()
        boot(settings)
        settings.refuse_remote()
        container = AppContainer.build(settings)
        await container.start()
        try:
            _, user, created = await container.managers.tenancy.add_member(
                command_request(settings),
                args.slug,
                args.email,
                args.name,
                Role(args.role),
            )
        finally:
            await container.close()
        if created:
            print(f"added {user.email} to org {args.slug} as {args.role}")
        else:
            print(f"{user.email} is already a member of org {args.slug}; nothing to do")
        return 0

    return asyncio.run(run())


def openapi(args: argparse.Namespace) -> int:
    settings = ApiSettings.model_validate({"_env_file": None, "environment": "test"})
    boot(settings)
    with tempfile.TemporaryDirectory() as tmp:
        container = AppContainer.for_tests(memory_storage(), InfraLocalImpl(Path(tmp)), settings)
        document = create_app(container).openapi()
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acme-api")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the API process")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)

    p_migrate = sub.add_parser(
        "migrate",
        help="apply migrations (--all, or --role <role>), or ensure-logins as the master",
    )
    p_migrate.add_argument(
        "action", nargs="?", default="upgrade", choices=["upgrade", "ensure-logins"]
    )
    p_migrate.add_argument("--role")
    p_migrate.add_argument("--all", action="store_true")

    p_boot = sub.add_parser("bootstrap", help="seed a fresh environment with one org and its owner")
    p_boot.add_argument("--org", required=True)
    p_boot.add_argument("--slug", required=True)
    p_boot.add_argument("--email", required=True)
    p_boot.add_argument("--name", required=True)
    p_boot.add_argument(
        "--operator",
        action="store_true",
        help="put the owner on the operator allowlist; local only, like every seed",
    )
    p_boot.add_argument(
        "--operator-role",
        default=OperatorRole.WRITE.value,
        choices=[r.value for r in OperatorRole],
        help="what the allowlist entry grants, with --operator; write includes read",
    )
    p_boot.add_argument(
        "--if-absent", action="store_true", help="succeed without changes when the slug exists"
    )

    p_member = sub.add_parser(
        "add-member", help="seed a person into an existing org; a no-op for a member"
    )
    p_member.add_argument("--slug", required=True, help="the org to join")
    p_member.add_argument("--email", required=True)
    p_member.add_argument("--name", required=True)
    # Neither OWNER (the org's own, minted by bootstrap) nor SERVICE (the role
    # a sweep's context carries, which update_membership_role refuses).
    p_member.add_argument(
        "--role",
        default="member",
        choices=[r.value for r in Role if r not in (Role.OWNER, Role.SERVICE)],
    )

    p_grant = sub.add_parser(
        "grant-operator",
        help="put an identity on the operator allowlist, disable its entry, or mint the "
        "provisioner's, the smoke identity's, or the local read operator's operator token",
    )
    what = p_grant.add_mutually_exclusive_group(required=True)
    what.add_argument(
        "--permission",
        choices=[r.value for r in OperatorRole],
        help="with --email: the entry to grant; write includes read",
    )
    what.add_argument("--disable", action="store_true", help="with --email: disable the entry")
    what.add_argument(
        "--mint-token",
        choices=(*TOKEN_HOLDERS, LOCAL_TOKEN_HOLDER),
        help="mint the identity's operator token into the secret store "
        "(acme-<env>-<holder>-token); printed only on a local database, and the "
        "local read operator's (operator) only there",
    )
    p_grant.add_argument(
        "--email",
        required=True,
        help="the identity: a person who signed up first, or one of the platform's own "
        "(@platform.acme.invalid), which the first grant makes",
    )
    p_grant.add_argument(
        "--expires-in", type=int, help="with --mint-token: seconds, 3600 when absent and at most"
    )

    p_openapi = sub.add_parser("openapi", help="emit the OpenAPI document")
    p_openapi.add_argument("--out", default="-")

    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(args)
    if args.command == "migrate":
        return migrate_roles(args)
    if args.command == "bootstrap":
        return bootstrap(args)
    if args.command == "add-member":
        return add_member(args)
    if args.command == "grant-operator":
        if args.expires_in is not None and not args.mint_token:
            parser.error("--expires-in goes with --mint-token")
        return grant_operator(args)
    return openapi(args)


if __name__ == "__main__":
    sys.exit(main())
