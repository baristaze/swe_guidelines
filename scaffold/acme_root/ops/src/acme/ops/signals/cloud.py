"""The cloud's stores: CloudWatch Logs Insights over `/acme/<env>/api`,
CloudWatch metrics in the `Acme` namespace, X-Ray trace summaries, and
Sentry's REST API under a token. The AWS clients come from an aioboto3
session under a named profile: the investigate role of the environment,
which can read every signal and write nothing.

The tracker is the one store the account does not provide: an environment
names it in its env file or names none. Without one the reader still reads
the logs, the metrics, and the traces, and reports the error event as not
read. There is one tracker project for the product and every environment
reports into it, so the reader names that project and filters the read on
this environment; nothing here is named per environment.

X-Ray is filtered on the annotation `acme_request_id`: the collector copies
the span attribute `acme.request_id` into `acme_request_id`, since X-Ray
filters only on a key of letters, digits, and underscores, and its X-Ray
exporter lists that copy under `indexed_attributes`. Without that, no filter
by id exists, and the reader says so instead of guessing from the URL."""

import asyncio
import re
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import aioboto3
import httpx

from acme.infra.aws_clients import client_config
from acme.ops.signals import ErrorEventFound, SignalsInterface, TraceFound
from acme.ops.signals.sentry import find_error_event

NAMESPACE = "Acme"
REQUEST_ID_ANNOTATION = "acme_request_id"
QUERY_POLL_SECONDS = 1.0
QUERY_POLL_LIMIT = 30
DEFAULT_LOOKBACK = timedelta(hours=1)
METRIC_QUERIES_PER_CALL = 500
"""The most queries one GetMetricData call takes."""


class SessionLike(Protocol):
    """What the impl needs of `aioboto3.Session`; a test hands a fake."""

    def client(
        self, service_name: str, *, config: Any = None
    ) -> AbstractAsyncContextManager[Any]: ...


AWS_TIMEOUT = timedelta(seconds=10)
"""Connect and read, per call, on every AWS client a reader opens."""


def log_group(environment: str) -> str:
    return f"/acme/{environment}/api"


def matches(dimensions: Mapping[str, str], labels: Mapping[str, str]) -> bool:
    """Every label is on the series: an exact value, or a `~` pattern that
    matches the whole value."""
    for key, wanted in labels.items():
        value = dimensions.get(key)
        if value is None:
            return False
        if wanted.startswith("~"):
            if re.fullmatch(wanted[1:], value) is None:
                return False
        elif value != wanted:
            return False
    return True


def insights_query(request_id: str) -> str:
    return (
        f'fields @timestamp, @message | filter request_id = "{request_id}" '
        "| sort @timestamp asc | limit 100"
    )


class SignalsCloudImpl(SignalsInterface):
    def __init__(
        self,
        *,
        environment: str,
        profile: str | None,
        region: str | None,
        sentry_url: str | None = None,
        sentry_token: str | None = None,
        sentry_org: str = "acme",
        sentry_project: str = "acme",
        session: SessionLike | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        lookback: timedelta = DEFAULT_LOOKBACK,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.environment = environment
        self.profile = profile
        self.region = region
        self.sentry_url = sentry_url.rstrip("/") if sentry_url else None
        self.sentry_token = sentry_token
        self.sentry_org = sentry_org
        self.sentry_project = sentry_project
        self.lookback = lookback
        self._session: SessionLike = session or cast(
            SessionLike, aioboto3.Session(profile_name=profile, region_name=region)
        )
        self._transport = transport
        self._now = now
        self._sleep = sleep

    @property
    def reads_error_events(self) -> bool:
        """A deployed environment gets an error tracker from its env file, and
        nothing provisions one, so it may name none. Logs, metrics, and traces
        come from the account itself and are read either way."""
        return bool(self.sentry_url and self.sentry_token)

    async def log_lines(self, request_id: str) -> list[str]:
        now = self._now()
        async with self._session.client("logs", config=client_config(AWS_TIMEOUT)) as logs:
            started = await logs.start_query(
                logGroupName=log_group(self.environment),
                startTime=int((now - self.lookback).timestamp()),
                endTime=int(now.timestamp()),
                queryString=insights_query(request_id),
            )
            for _ in range(QUERY_POLL_LIMIT):
                results = await logs.get_query_results(queryId=started["queryId"])
                if results.get("status") in ("Complete", "Failed", "Cancelled", "Timeout"):
                    break
                await self._sleep(QUERY_POLL_SECONDS)
            else:
                results = {"results": []}
        lines: list[str] = []
        for row in results.get("results", []):
            fields = {cell.get("field"): cell.get("value") for cell in row}
            if fields.get("@message") is not None:
                lines.append(str(fields["@message"]))
        return lines

    async def metric_delta(
        self, name: str, labels: Mapping[str, str], since: datetime
    ) -> float | None:
        """The collector exports every series with all its labels as
        dimensions (`NoDimensionRollup`), and CloudWatch matches dimensions
        exactly, so no single query names "every series of this counter".
        The series of this environment are listed by name, the labels picked
        among them (a `~` value as a pattern over the whole value, as
        Prometheus reads it), and their sums over the window added up."""
        now = self._now()
        period = max(60, int(((now - since).total_seconds() // 60 + 1) * 60))
        config = client_config(AWS_TIMEOUT)
        async with self._session.client("cloudwatch", config=config) as cloudwatch:
            series = [
                dimensions
                for dimensions in await self._series(cloudwatch, name)
                if matches(dimensions, labels)
            ]
            if not series:
                return None
            total = 0.0
            for first in range(0, len(series), METRIC_QUERIES_PER_CALL):
                batch = series[first : first + METRIC_QUERIES_PER_CALL]
                data = await cloudwatch.get_metric_data(
                    MetricDataQueries=[
                        {
                            "Id": f"m{index}",
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": NAMESPACE,
                                    "MetricName": name,
                                    "Dimensions": [
                                        {"Name": key, "Value": value}
                                        for key, value in sorted(dimensions.items())
                                    ],
                                },
                                "Period": period,
                                "Stat": "Sum",
                            },
                            "ReturnData": True,
                        }
                        for index, dimensions in enumerate(batch)
                    ],
                    StartTime=since,
                    EndTime=now,
                )
                for result in data.get("MetricDataResults", []):
                    total += sum(float(v) for v in result.get("Values", []))
        return total

    async def _series(self, cloudwatch: Any, name: str) -> list[dict[str, str]]:
        """Every series of the metric that carries this environment's
        dimension, as its dimensions; CloudWatch lists what it saw in the last
        two weeks, which a window of hours is inside."""
        found: list[dict[str, str]] = []
        request: dict[str, Any] = {
            "Namespace": NAMESPACE,
            "MetricName": name,
            "Dimensions": [{"Name": "environment", "Value": self.environment}],
        }
        while True:
            page = await cloudwatch.list_metrics(**request)
            for metric in page.get("Metrics", []):
                found.append({d["Name"]: d["Value"] for d in metric.get("Dimensions", [])})
            token = page.get("NextToken")
            if not token:
                return found
            request["NextToken"] = token

    async def trace(self, request_id: str) -> TraceFound | None:
        now = self._now()
        async with self._session.client("xray", config=client_config(AWS_TIMEOUT)) as xray:
            summaries = await xray.get_trace_summaries(
                StartTime=now - self.lookback,
                EndTime=now,
                FilterExpression=f'annotation.{REQUEST_ID_ANNOTATION} = "{request_id}"',
            )
        for summary in summaries.get("TraceSummaries", []):
            trace_id = str(summary.get("Id", ""))
            names = tuple(
                str(service.get("Name", ""))
                for service in summary.get("ServiceIds", [])
                if service.get("Name")
            )
            return TraceFound(trace_id=trace_id, span_names=names)
        return None

    async def error_event(self, request_id: str) -> ErrorEventFound | None:
        """None when the environment names no tracker: nothing was read, and
        the caller says that rather than calling the leg empty. The read is
        scoped to this environment, so another environment's events in the
        same project are never answered with."""
        if not (self.sentry_url and self.sentry_token):
            return None
        async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as http:
            return await find_error_event(
                http,
                self.sentry_url,
                self.sentry_token,
                self.sentry_org,
                self.sentry_project,
                self.environment,
                request_id,
            )

    def describe(self) -> str:
        errors = (
            f"errors: Sentry {self.sentry_url} org {self.sentry_org} "
            f"project {self.sentry_project} by environment {self.environment} and tag request_id"
            if self.reads_error_events
            else "errors: not read, the environment names no error tracker"
        )
        return (
            f"logs: CloudWatch Logs Insights on {log_group(self.environment)} by request_id; "
            f"metrics: CloudWatch namespace {NAMESPACE}; "
            f"traces: X-Ray by annotation {REQUEST_ID_ANNOTATION} "
            "(the collector must index acme.request_id; otherwise no trace is found by id); "
            f"{errors}; "
            f"profile {self.profile or 'default'}, region {self.region or 'default'}"
        )
