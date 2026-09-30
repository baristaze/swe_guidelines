"""The error tracker never receives a secret: no frame locals, no request
body, no personal data, and a scrubber over what an event still carries. Nor
does it receive what a caller wrote as a request's path, query, or headers."""

from acme.infra.observability import ERROR_REPORTING_PRIVACY, outgoing_event, request_id_var


def test_the_tracker_gets_no_locals_no_body_and_no_personal_data() -> None:
    assert ERROR_REPORTING_PRIVACY["include_local_variables"] is False
    assert ERROR_REPORTING_PRIVACY["send_default_pii"] is False
    assert ERROR_REPORTING_PRIVACY["max_request_body_size"] == "never"


def test_the_scrubber_blanks_bearers_cookies_and_passwords_wherever_they_sit() -> None:
    event = {
        "request": {
            "headers": {"Authorization": "Bearer tok", "Cookie": "s=1", "Accept": "*/*"},
            "data": {"password": "pswd_1234"},
        },
        "extra": {"nested": {"token": "abc", "database_url": "postgresql://u:p@h/d"}},
    }
    ERROR_REPORTING_PRIVACY["event_scrubber"].scrub_event(event)
    headers = event["request"]["headers"]
    assert headers["Authorization"] != "Bearer tok" and headers["Cookie"] != "s=1"
    assert headers["Accept"] == "*/*"
    assert event["request"]["data"]["password"] != "pswd_1234"
    assert event["extra"]["nested"]["token"] != "abc"
    assert "p@h" not in str(event["extra"]["nested"]["database_url"])


def test_an_event_leaves_with_the_request_id_and_nothing_the_caller_wrote() -> None:
    """The SDK's web integration fills `request` from the request: the URL,
    the query string, the headers. A caller writes each, and the tracker shows
    an event to whoever looks into the request, so only the method leaves."""
    words = "WIPZ_DOWN_ALL_RULZ"
    event = {
        "transaction": f"https://api.acme.example/{words}",
        "transaction_info": {"source": "url"},
        "request": {
            "method": "GET",
            "url": f"https://api.acme.example/{words}",
            "query_string": f"say={words}",
            "headers": {"user-agent": words, "x-note": words},
        },
    }
    token = request_id_var.set("0199aaaa-0000-7000-8000-000000000001")
    try:
        sent = outgoing_event(event, {})
    finally:
        request_id_var.reset(token)
    assert sent["request"] == {"method": "GET"}
    assert sent["transaction"] == "unmatched"
    assert sent["tags"] == {"request_id": "0199aaaa-0000-7000-8000-000000000001"}
    assert words not in str(sent)


def test_an_event_of_a_matched_route_keeps_its_transaction() -> None:
    event = {"transaction": "/v1/orgs/{org_id}", "transaction_info": {"source": "route"}}
    assert outgoing_event(event, {})["transaction"] == "/v1/orgs/{org_id}"
