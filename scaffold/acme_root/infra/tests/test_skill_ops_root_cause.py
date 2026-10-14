"""`ops-root-cause` opens one tenant through the operator plane, follows
each request id once across every signal, and keeps no text the tenant
wrote: each read it makes is a command of the skill, and each choice two
runs could make two ways is a sentence of it."""

import re
import shutil

import pytest
from skills_support import OPERATOR_READ, allowed_tools, jq, prose_of, text_of

NAME = "ops-root-cause"

# What a tenant writes and the operator plane answers with: an org's name and
# slug, a member's display name and address. A tenant chooses those words, and
# the session that reads them holds an operator's token.
TENANT_TEXT = {"name", "slug", "display_name", "email"}
TENANT_READ = re.compile(
    r'^ *curl [^\n]*"\$ACME_API_URL/v1/admin/orgs/<org_id>(?:/members(?:\?cursor=<next_cursor>)?)?"'
    r"(?P<piped> \\\n +\| jq '(?P<kept>[^'\n]*)'$)?",
    re.MULTILINE,
)


def test_the_root_cause_reads_of_a_tenant_keep_no_text_the_tenant_wrote() -> None:
    """The org, its members, and a next page of them are read through `jq`,
    which keeps the ids, the kind, and the timestamps: no read is printed
    whole."""
    reads = list(TENANT_READ.finditer(text_of(NAME)))
    assert len(reads) == 3, (
        "ops-root-cause no longer reads the org and its members as this test sees them"
    )
    for read in reads:
        assert read["piped"], f"a read of the tenant is printed whole: {read[0]}"
        assert not set(re.findall(r"[a-z_]+", read["kept"])) & TENANT_TEXT, read["kept"]
    assert "Bash(jq:*)" in allowed_tools(NAME)
    assert "never run either read without its `jq`" in prose_of(NAME)
    assert "**Tenant.** <kind> org" in text_of(NAME)


ROOT_CAUSE_READS = [
    "me",
    "orgs/<org_id>",
    "orgs/<org_id>/members",
    "orgs/<org_id>/members?cursor=<next_cursor>",
    "orgs/<org_id>/change-events?after_seq=<n>&limit=1",
    "orgs/<org_id>/change-events?after_seq=<seq>&limit=200",
]


def test_the_root_cause_writes_each_read_it_makes() -> None:
    """The operator, the tenant, its members and their next page, a probe of
    the feed, and a page of it: each is a command of the skill, so no run
    writes its own."""
    reads = [read["route"] for read in OPERATOR_READ.finditer(text_of(NAME))]
    assert reads == ROOT_CAUSE_READS


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_the_root_cause_reads_print_what_the_report_needs() -> None:
    """Each filter runs here as the skill writes it. The operator's address
    leaves as its domain alone, a probe that finds no event says so, a page
    says how long it is and where the next one starts, and every read prints a
    refusal as its code."""
    kept = {read["route"]: read["kept"] for read in OPERATOR_READ.finditer(text_of(NAME))}
    me = {"identity_id": "0" * 32, "email": "sam@example.test", "operator_role": "read"}
    assert jq(kept["me"], me) == {
        "operator_role": "read",
        "email_domain": "example.test",
        "error": None,
    }
    event = {
        "seq": 7,
        "kind": "tenancy.org.updated",
        "target_id": "1" * 32,
        "produced_at": "2026-01-01T00:00:00Z",
        "actor_id": "2" * 32,
        "request_id": "3" * 32,
        "app": "portal",
    }
    probe = kept["orgs/<org_id>/change-events?after_seq=<n>&limit=1"]
    assert jq(probe, [event]) == {"seq": 7, "produced_at": "2026-01-01T00:00:00Z"}
    assert jq(probe, []) == {"seq": None, "produced_at": None}
    page = kept["orgs/<org_id>/change-events?after_seq=<seq>&limit=200"]
    assert jq(page, [event]) == {"count": 1, "last_seq": 7, "change_events": [event]}
    assert jq(page, []) == {"count": 0, "last_seq": None, "change_events": []}
    # A 401 prints as its code, which is how the run knows its token expired.
    for code in ("not_found", "not_authenticated"):
        refusal = {"error": {"code": code, "message": code, "request_id": "4" * 32}}
        for route, program in kept.items():
            printed = jq(program, refusal)
            assert isinstance(printed, dict) and printed["error"] == code, route


# The two reads of the error tracker a pass makes: the issues of the request,
# and the events of each issue. An issue's title and an event's exception carry
# the exception's text, which can quote what the tenant sent.
TRACKER_READ = re.compile(
    r'^ +"\$ACME_ERROR_TRACKER_URL/api/0/(?P<route>[^"]*)"'
    r"(?P<piped> \\\n +\| jq (?:-c )?'(?P<kept>[^'\n]*)'$)?",
    re.MULTILINE,
)
ISSUES = "projects/$ACME_ERROR_TRACKER_ORG/$ACME_ERROR_TRACKER_PROJECT/issues/"
EVENTS = "issues/<issue id>/events/"


def test_the_root_cause_reads_the_tracker_through_jq_for_this_request_alone() -> None:
    reads = list(TRACKER_READ.finditer(text_of(NAME)))
    assert [read["route"] for read in reads] == [ISSUES, EVENTS]
    for read in reads:
        assert read["piped"], f"a read of the tracker is printed whole: {read[0]}"
    # Sentry answers a page of the issue's events: the two filters put this
    # request's event on it, and `full=true` puts its stack in it.
    assert (
        '--data-urlencode "environment=<env>" --data-urlencode "query=request_id:<id>"'
        in text_of(NAME)
    )
    assert '--data-urlencode "full=true"' in text_of(NAME)


def _tracker_event(request_id: str, environment: str, said: str) -> dict[str, object]:
    """An event as the tracker answers it with `full=true`: four frames of the
    product's code under one of a library's, and the exception's text."""
    frames = [
        {"filename": f"app/{name}.py", "lineNo": line, "function": name, "inApp": True}
        for line, name in enumerate("abcd", start=1)
    ]
    frames.append({"filename": "lib/e.py", "lineNo": 5, "function": "e", "inApp": False})
    tags = {"request_id": request_id, "environment": environment, "release": "api@1"}
    exception = {"type": "ValidationError", "value": said, "stacktrace": {"frames": frames}}
    return {
        "eventID": f"{request_id}-{environment}",
        "dateCreated": "2026-01-01T00:00:00Z",
        "tags": [{"key": key, "value": value} for key, value in tags.items()],
        "entries": [{"type": "exception", "data": {"values": [exception]}}],
    }


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_the_root_cause_tracker_reads_keep_no_text_of_the_exception() -> None:
    """The issues keep their id, when they were last seen, and how often; the
    events keep the one of this request in this environment, its type, and
    the last three frames of the product's code. A refusal prints as itself."""
    kept = {
        read["route"]: read["kept"].replace("<id>", "r-1").replace("<env>", "local")
        for read in TRACKER_READ.finditer(text_of(NAME))
    }
    said = "1 validation error, input_value={'email': 'sam@example.test'}"
    issue = {
        "id": "7",
        "title": f"ValidationError: {said}",
        "culprit": said,
        "metadata": {"value": said},
        "lastSeen": "2026-01-01T00:00:00Z",
        "count": "4",
    }
    assert jq(kept[ISSUES], [issue]) == {
        "issues": [{"id": "7", "lastSeen": "2026-01-01T00:00:00Z", "count": "4"}]
    }
    page = [
        _tracker_event("r-1", "local", said),
        _tracker_event("r-1", "production", said),
        _tracker_event("r-2", "local", said),
    ]
    frames = [
        {"filename": f"app/{name}.py", "lineNo": line, "function": name}
        for line, name in enumerate("bcd", start=2)
    ]
    assert jq(kept[EVENTS], page) == {
        "events": [
            {
                "id": "r-1-local",
                "at": "2026-01-01T00:00:00Z",
                "release": "api@1",
                "exception": [{"type": "ValidationError", "frames": frames}],
            }
        ]
    }
    assert jq(kept[EVENTS], []) == {"events": []}
    for program in kept.values():
        assert jq(program, {"detail": "Unauthorized"}) == {"error": "Unauthorized"}


# Where two runs of the root cause could read two things or end two ways:
# each is a sentence of the skill.
ROOT_CAUSE_DECIDES = [
    "The tracker holds no tenant",
    "never an org id, so it is never read by the tenant or by a tag guessed for one",
    "Without `--request-id`, when the window's change events hold no request tied to the symptom, "
    "the run makes no pass",
    "so a run whose only such change events are writes that landed still makes its passes",
    "which a read through its `jq` prints as the error code `not_authenticated`",
    'The pass writes its error leg as "not read", with which of the three it was, '
    "and goes on to the logs",
    "--since <start_at> --until <end_at> api maintenance",
    "so `<n>` is the minutes from the window's `start` of step 3 to now, rounded up",
    "with the request id the tenant saw as `--request-id`",
    "The run never widens the window itself",
    "The next page reads from the page's `last_seq`, until a page's `count` is under 200",
    "The run goes on only on `operator_role: read`.",
    "The window ends when this step starts and begins `--since` before it, both read once",
    "carry the exception's text, which can quote what the tenant sent, "
    "so the `jq` keeps the issue's `id`, `lastSeen`, and `count` alone",
    "the last three frames of the product's code, never the exception's text",
]


@pytest.mark.parametrize("sentence", ROOT_CAUSE_DECIDES)
def test_the_root_cause_leaves_no_read_to_the_run(sentence: str) -> None:
    assert sentence in prose_of(NAME), f"ops-root-cause no longer says: {sentence}"


# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "A run follows at most 5 request ids, one pass each",
    "It lists every id past the fifth as not followed",
    "Poll `get-query-results` at most 10 times for one query, each poll after `sleep 5`",
    'writes its log leg as "not read: the query did not finish in 10 polls"',
    "Probe with `limit=1`",
    'the pass ends with "not found" for its id',
    "never more than 5 request ids, never a second pass over one, "
    "never more than 10 polls of a query",
    "When `next_cursor` is not null, read the next page with it as `cursor`",
    "Read at most 20 pages of members, 1,000 of them.",
    "never more than 20 pages of members",
    "An answer that is empty, or that `jq` cannot parse, is no answer.",
    "Either way the run ends there, as on a refusal",
    "The read is not made a second time.",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"
