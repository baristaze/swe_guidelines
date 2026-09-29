"""The Sentry-shaped read both impls share: GlitchTip locally and Sentry in
the cloud answer the same issue and event routes, under a token. There is one
project for the product, and every environment reports into it, so a read
names the project and filters on the environment: `environment:<name>` in the
issue search, and the event's own `environment` tag as the proof. An issue in
a shared project can hold events from more than one environment, which is why
the environment is checked on the event and not only on the issue. The event
that answers is the one whose `request_id` tag is the id the platform put on
every event of that request."""

from typing import Any

import httpx

from acme.ops.signals import ErrorEventFound

RECENT_ISSUES = 25


async def find_error_event(
    http: httpx.AsyncClient,
    base_url: str,
    token: str,
    org: str,
    project: str,
    environment: str,
    request_id: str,
) -> ErrorEventFound | None:
    """The issues of the product's project in this environment: first the ones
    the id names, then the recent ones, and in each the event that carries both
    the id and the environment. The search is a convenience; the two tags are
    the proof."""
    headers = {"Authorization": f"Bearer {token}"}
    base = base_url.rstrip("/")
    issues_url = f"{base}/api/0/projects/{org}/{project}/issues/"
    seen: set[str] = set()
    for params in (
        {"query": f"environment:{environment} request_id:{request_id}", "limit": RECENT_ISSUES},
        {"query": f"environment:{environment}", "limit": RECENT_ISSUES, "sort": "-last_seen"},
    ):
        response = await http.get(issues_url, params=params, headers=headers)
        response.raise_for_status()
        for issue in response.json():
            issue_id = str(issue["id"])
            if issue_id in seen:
                continue
            seen.add(issue_id)
            events = await http.get(
                f"{base}/api/0/issues/{issue_id}/events/",
                params={"environment": environment},
                headers=headers,
            )
            events.raise_for_status()
            for event in events.json():
                if (
                    tag_value(event, "request_id") == request_id
                    and tag_value(event, "environment") == environment
                ):
                    return ErrorEventFound(
                        event_id=str(event.get("eventID") or event.get("id")),
                        issue_id=issue_id,
                        title=str(issue.get("title", "")),
                    )
    return None


def tag_value(event: dict[str, Any], key: str) -> str | None:
    tags = event.get("tags") or []
    if isinstance(tags, dict):
        value = tags.get(key)
        return None if value is None else str(value)
    for tag in tags:
        if isinstance(tag, dict) and tag.get("key") == key:
            return None if tag.get("value") is None else str(tag["value"])
    return None
