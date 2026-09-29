"""The error tracker never receives a secret: no frame locals, no request
body, no personal data, and a scrubber over what an event still carries."""

from acme.infra.observability import ERROR_REPORTING_PRIVACY


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
