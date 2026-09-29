"""The one settings object of the API process: storage, infra, and the
knobs that belong to this service, all under the ACME_ prefix."""

import ipaddress
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from acme.infra.impl.settings import CLOUD_ENVIRONMENTS, InfraSettings
from acme.integrations.settings import IntegrationsSettings
from acme.om.storage.settings import StorageSettings

DEV_SIGN_IN_ENVIRONMENTS = frozenset({"local", "test"})
"""Where the sign-in by address alone may be on. Anywhere else it is
refused at boot, the way a local backend is."""


class ApiSettings(StorageSettings, InfraSettings, IntegrationsSettings):
    model_config = SettingsConfigDict(env_prefix="ACME_", env_file=".env", extra="ignore")

    service_name: str = "api"
    # The namespaces whose routers this process mounts; empty mounts every one
    # the image hosts (tenancy, events, media). Naming a subset is how
    # one namespace becomes a service of its own: the same image, another
    # value, and no code change. A name the image does not host refuses the
    # boot.
    namespaces: list[str] = []
    version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # The proxies whose X-Forwarded-For names the client, as addresses or
    # CIDR blocks; never "*", which trusts every peer and so lets any caller
    # choose its own address for the rate limits. Empty, the peer is the
    # client and the header is ignored; the cloud passes the VPC block the
    # load balancer lives in.
    trusted_proxies: list[str] = []
    # What the portal's CDN distribution sends in the X-Acme-Edge header on
    # every request it forwards. The distribution appends the viewer's address
    # to X-Forwarded-For and the load balancer appends the distribution's, so
    # the trusted proxies above name the edge as the client; beside this
    # value, and only beside it, the address the edge appended names the
    # client instead. Any CDN customer's distribution appends an address, and
    # its owner can write the header before it, so the edge's own address
    # range is never trusted. Unset, a request through the edge is counted by
    # the edge's address. It needs trusted proxies: the edge is one hop
    # further in than they are.
    edge_secret: SecretStr | None = None

    # Every number in this file is illustrative, and deliberately generous:
    # this system is here to show the shape, and a limit that trips during a
    # demo teaches the wrong lesson. A team adopting it sets its own from
    # what its traffic does. What is not illustrative is where each bound
    # sits and what it protects.
    #
    # This budget is per address, and one address is a crowd: a company
    # behind one gateway, a school, a shared runner. So it is sized for a
    # crowd signing in, not for one person. It covers every sign-in route:
    # the start, the callback, the device sign-in and its polls, the local
    # sign-in, and the second factor. The identity provider guards its own
    # sign-in; the defence against guessing a second factor is the per-email
    # delay below, which no crowd dilutes.
    login_rate_limit: int = 2000
    login_rate_window_seconds: int = 60
    # Each session and each api key has a budget of its own, reads (GET,
    # HEAD) apart from writes, over one window. The subject is the
    # credential, so a crowd behind one address never shares it, and it is
    # sized for the busiest honest client: a tab that loads a page, follows
    # its hints, and refetches on focus, or a traffic run's stress profile at
    # its fastest. An integration that polls once a second spends 60 of it.
    credential_rate_limit_reads: int = Field(default=3000, gt=0)
    credential_rate_limit_writes: int = Field(default=1200, gt=0)
    credential_rate_window_seconds: int = Field(default=60, gt=0)
    # Each address has a budget of failed authentications: a bearer the API
    # looked up and refused, because it is unknown, expired, or revoked. Once
    # it is spent, a request from that address is refused before its
    # credential is looked up, until the window ends. One address is a crowd
    # here too, and a crowd back from a weekend sends a burst of expired
    # sessions, one per open call of each tab, so it is sized for that.
    failed_authentication_limit: int = Field(default=1000, gt=0)
    failed_authentication_window_seconds: int = Field(default=60, gt=0)
    # Past the per-address limit, which rides the cache and fails open: a run
    # of wrong second-factor codes for one email makes the next one wait,
    # counted in the database. The first `sign_in_free_failures` cost
    # nothing; then the wait starts at the base and doubles, up to the cap.
    sign_in_free_failures: int = Field(default=10, ge=1)
    sign_in_delay_base_seconds: float = Field(default=1.0, gt=0)
    sign_in_delay_cap_seconds: float = Field(default=30.0, gt=0)
    # How long a sign-in lasts before it must be exchanged for a session, and
    # how long a session lasts: from its exchange, whatever it is used for
    # (the absolute lifetime), and from its last use (the idle one). A
    # session ends at whichever passes first: 30 days from its exchange, or 14
    # days without a request (ADR 0063).
    login_lifetime_seconds: int = Field(default=600, gt=0)
    session_lifetime_seconds: int = Field(default=2592000, gt=0)
    session_idle_lifetime_seconds: int = Field(default=1209600, gt=0)
    # The longest an operator token lives, and what a mint that names no
    # lifetime gets. An hour at most, whoever mints it.
    operator_token_max_lifetime_seconds: int = Field(default=3600, gt=0, le=3600)
    # The key the operators' TOTP secrets are sealed under: a Fernet-shaped
    # key (URL-safe base64 of 32 bytes), a process credential injected at
    # start from the secret store like the database URL, and only into the
    # API. No default: a process without it refuses every enrolment and every
    # sign-in that presents a code, and says so; one of the wrong shape
    # refuses to start.
    totp_encryption_key: SecretStr | None = None
    # Where a sign-in at the identity provider may come back to: this
    # environment's portal callback. A deployed environment names its own
    # and nothing else, over https, never a local address; the local stack
    # names the portal's two local ports. A redirect not named here is
    # refused, so a code is never sent anywhere else.
    sign_in_redirect_uris: list[str] = [
        "http://localhost:55173/auth/callback",
        "http://localhost:5173/auth/callback",
    ]
    # Where the identity provider's logout may send a person back to: this
    # environment's portal page that says they are signed out. Each is also
    # one of the WorkOS application's sign-out URIs. A deployed environment
    # names its own and nothing else, over https; the local stack names the
    # portal's two local ports. A return not named here is refused.
    sign_out_return_uris: list[str] = [
        "http://localhost:55173/signed-out",
        "http://localhost:5173/signed-out",
    ]
    # The sign-in by address alone, with no browser round trip, for the
    # seed, the traffic generator, and the tests. Off unless set, and
    # refused at boot outside a local or test environment.
    dev_sign_in_enabled: bool = False
    # How long an invitation's link works, from its send or resend.
    invitation_lifetime_days: int = Field(default=7, ge=1, le=30)
    # The interactive API docs and the OpenAPI document, served locally for
    # a developer. A deployed environment turns them off: the document is in
    # the repository, and a production edge has no use for a console.
    interactive_docs: bool = True
    # The socket's two send lanes, each bounded on its own. The stream lane
    # holds the event hints, and a full one drops its oldest: the client that
    # sees the gap replays from storage. The control lane holds the frames
    # that say where the socket stands (hello, pong, subscribed,
    # unsubscribed, error), and it is small because a socket offers few of
    # them; a lane that fills is a fault of the process, not a burst.
    realtime_send_buffer_size: int = Field(default=256, gt=0)
    realtime_control_buffer_size: int = Field(default=16, gt=0)
    # How often an open socket asks again whether the credential behind it
    # still holds, in seconds. The bus closes a revoked socket at once; this
    # is the bound when the bus lost the message, and it costs each socket
    # one recheck per interval.
    realtime_recheck_seconds: float = Field(default=300.0, gt=0)
    # How long the tenant's head this process heard on the bus answers a
    # ping without a read, in seconds. It is the longest a hint the bus lost
    # stays hidden from a quiet socket. Zero reads the head on every ping.
    realtime_head_max_age_seconds: float = Field(default=60.0, ge=0)
    # The readiness probe's own deadline, shorter than its prober's timeout
    # (the deploy's check after a rollout gives up at 10 seconds), so a hung
    # database or an exhausted pool makes the probe answer "not ready"
    # instead of making it stop answering. Seconds.
    readiness_timeout_seconds: float = 2.0
    # Admission: the requests this process keeps in flight at once, counted
    # in two budgets, and what a refusal past either tells the client to
    # wait. Reads (GET, HEAD) and writes are budgeted apart so that a read
    # storm, which is what a client that was offline replaying its backlog
    # is, cannot take every slot from the commands. Together they are the one
    # bound the process had, which is a multiple of what it can actually work
    # on: the declared size of the pool it opens per role and no more, since
    # the pools carry no overflow. Reads are the many and writes the few, so
    # that is how the bound is split. A burst still waits briefly on a
    # checkout, which has a bound of its own, and only a flood is refused; it
    # is not the rate limits above, which are fairness between subjects and
    # fail open.
    admission_limit_reads: int = Field(default=400, gt=0)
    admission_limit_writes: int = Field(default=200, gt=0)
    admission_retry_after_seconds: int = 1
    # The deadline of an admitted request, from the moment it takes its slot:
    # every call it makes to a provider or to AWS shares it, and a call still
    # waiting when it passes ends there, so a provider that hangs costs a
    # request this at most instead of its timeouts times its retries (ADR
    # 0069). It sits inside every bound on how long an answer can still reach
    # someone: the portal and the command line give up on a call at 30
    # seconds, a draining task has 45 (15 of deregistration, 30 to stop), and
    # the load balancer 60. The 10 seconds left under the clients' 30 carry
    # the refusal back and the database work around the calls. Seconds.
    request_deadline_seconds: float = Field(default=20.0, gt=0)

    @model_validator(mode="after")
    def local_doors_stay_local(self) -> ApiSettings:
        """The local sign-in is refused outside a local or test environment,
        and a deployed environment's sign-in and sign-out come back to its own
        https address and nowhere else. Each refusal names the setting."""
        if self.dev_sign_in_enabled and self.environment not in DEV_SIGN_IN_ENVIRONMENTS:
            raise ValueError(
                "ACME_DEV_SIGN_IN_ENABLED=true is refused when "
                + f"ACME_ENVIRONMENT={self.environment}"
            )
        if self.environment in CLOUD_ENVIRONMENTS:
            for setting, uris, what in (
                ("ACME_SIGN_IN_REDIRECT_URIS", self.sign_in_redirect_uris, "sign-in"),
                ("ACME_SIGN_OUT_RETURN_URIS", self.sign_out_return_uris, "sign-out"),
            ):
                for uri in uris:
                    parts = urlsplit(uri)
                    if parts.scheme != "https" or parts.hostname in (
                        None,
                        "localhost",
                        "127.0.0.1",
                    ):
                        raise ValueError(
                            f"{setting} names {uri!r}; a deployed environment's "
                            f"{what} comes back to its own https address"
                        )
        return self

    @model_validator(mode="after")
    def the_edge_stands_behind_a_trusted_proxy(self) -> ApiSettings:
        """The edge's hop is counted from the trusted proxies' hops, so an
        edge secret with no trusted proxy has nothing to count from."""
        if self.edge_secret is not None and not self.trusted_proxies:
            raise ValueError("edge_secret is set and trusted_proxies is empty; the edge needs both")
        if self.edge_secret is not None and len(self.edge_secret.get_secret_value()) < 32:
            raise ValueError("edge_secret is shorter than 32 characters")
        return self

    @field_validator("trusted_proxies")
    @classmethod
    def proxies_are_addresses_or_blocks(cls, proxies: list[str]) -> list[str]:
        """Each proxy is an address or a CIDR block. uvicorn takes a wildcard as
        trust in every peer, and a name it cannot parse as a literal host, so
        both are refused here, where the mistake is one line to find."""
        for proxy in proxies:
            try:
                ipaddress.ip_network(proxy)
            except ValueError:
                raise ValueError(
                    f"trusted_proxies names {proxy!r}; each proxy is an address or a CIDR block"
                ) from None
        return proxies
