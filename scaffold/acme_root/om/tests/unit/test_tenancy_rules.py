"""The tenancy namespace's pure rules."""

from datetime import UTC, datetime, timedelta

import pytest

from acme.om.tenancy.rules import (
    MAX_SLUG_LENGTH,
    SLUG_PATTERN,
    check_email,
    check_org,
    check_time_zone,
    email_digest,
    email_domain,
    fold_email,
    is_platform_email,
    matching_totp_step,
    otpauth_uri,
    personal_org_name,
    sign_in_delay,
    slug_from_name,
    sso_joins,
    totp_code,
    totp_step,
)

KNOBS = {"free": 3, "base": timedelta(seconds=1), "cap": timedelta(minutes=5)}


def test_the_sign_in_delay_grows_from_the_run_and_stops_at_its_cap() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    assert sign_in_delay(2, now, now, **KNOBS) == timedelta(0)
    assert sign_in_delay(3, now, now, **KNOBS) == timedelta(seconds=1)
    assert sign_in_delay(4, now, now, **KNOBS) == timedelta(seconds=2)
    assert sign_in_delay(8, now, now, **KNOBS) == timedelta(seconds=32)
    assert sign_in_delay(40, now, now, **KNOBS) == timedelta(minutes=5)


def test_the_sign_in_delay_counts_from_the_last_failure() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    assert sign_in_delay(4, now - timedelta(seconds=1), now, **KNOBS) == timedelta(seconds=1)
    assert sign_in_delay(4, now - timedelta(minutes=1), now, **KNOBS) == timedelta(0)
    assert sign_in_delay(9, None, now, **KNOBS) == timedelta(0)


def test_a_totp_code_is_rfc_6238s() -> None:
    """RFC 6238's SHA-1 test vector, cut to the six digits authenticators show:
    at 59 seconds the eight-digit code is 94287082."""
    secret = b"12345678901234567890"
    at = datetime(1970, 1, 1, 0, 0, 59, tzinfo=UTC)
    assert totp_step(at) == 1
    assert totp_code(secret, 1) == "287082"
    assert totp_code(secret, totp_step(datetime(2009, 2, 13, 23, 31, 30, tzinfo=UTC))) == "005924"


def test_a_code_matches_one_step_either_side_and_nothing_else() -> None:
    secret = b"12345678901234567890"
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    step = totp_step(now)
    for offset in (-1, 0, 1):
        assert matching_totp_step(secret, totp_code(secret, step + offset), now) == step + offset
    assert matching_totp_step(secret, totp_code(secret, step + 2), now) is None
    assert matching_totp_step(secret, "12345", now) is None
    assert matching_totp_step(secret, "abcdef", now) is None
    # Digits of another script are not a code, and never reach the compare.
    fullwidth = totp_code(secret, step).translate({48 + d: 0xFF10 + d for d in range(10)})
    assert fullwidth.isdigit() and matching_totp_step(secret, fullwidth, now) is None


def test_the_otpauth_uri_carries_the_secret_the_account_and_the_issuer() -> None:
    uri = otpauth_uri(b"12345678901234567890", "root@example.test", "Big Co")
    assert uri == (
        "otpauth://totp/Big%20Co%3Aroot%40example.test?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        "&issuer=Big%20Co&algorithm=SHA1&digits=6&period=30"
    )


def test_an_address_folds_to_lower_case_all_of_it() -> None:
    """The local part and the domain alike, by Unicode's full mapping, which
    is what `lower(... COLLATE pg_unicode_fast)` gives in the database."""
    assert fold_email("Ann.Lee@Example.TEST") == "ann.lee@example.test"
    assert fold_email("ÜNAL@Örnek.test") == "ünal@örnek.test"
    assert fold_email("İPEK@x.test") == "i\u0307pek@x.test"
    assert fold_email("ann@example.test") == "ann@example.test"


def test_the_email_digest_is_the_sha_256_of_the_folded_address() -> None:
    assert email_digest("ann@example.test") == (
        "eff90234b5c0d7bb3000e7e2faa01214ffecef55617da623def7482021cc124f"
    )
    assert email_digest("Ann@EXAMPLE.test") == email_digest("ann@example.test")
    assert email_digest("ann@example.test") != email_digest("anne@example.test")


@pytest.mark.parametrize(
    "email", ["ann", "ann@", "@example.test", "ann@example", " ann@example.test", "a\\b@x.test"]
)
def test_an_address_of_the_wrong_shape_is_refused(email: str) -> None:
    with pytest.raises(ValueError):
        check_email(email)


def test_the_platforms_own_addresses_are_told_apart() -> None:
    assert is_platform_email("provisioner@platform.acme.invalid")
    assert is_platform_email("Smoke@Platform.Acme.Invalid")
    assert not is_platform_email("provisioner@example.test")


def test_the_domain_of_an_address_is_its_part_after_the_at_in_lower_case() -> None:
    assert email_domain("Ann@Ajax.Example") == "ajax.example"
    assert email_domain("a@b@c.test") == "c.test"


def test_a_single_sign_on_joins_only_an_address_in_a_verified_domain() -> None:
    assert sso_joins("ann@ajax.example", ("ajax.example",))
    assert sso_joins("Ann@AJAX.example", ("Ajax.Example", "other.example"))
    assert not sso_joins("ann@evil.example", ("ajax.example",))
    assert not sso_joins("ann@sub.ajax.example", ("ajax.example",))
    assert not sso_joins("ann@ajax.example", ())


def test_a_slug_nobody_typed_is_the_name_and_a_tail() -> None:
    assert slug_from_name("Dee's Bakery", "abcd1234") == "dee-s-bakery-abcd1234"
    assert slug_from_name("  Café Zürich  ", "x") == "cafe-zurich-x"
    assert slug_from_name("!!!", "abcd1234") == "org-abcd1234"
    long = slug_from_name("a" * 60, "abcd1234")
    assert len(long) == MAX_SLUG_LENGTH and SLUG_PATTERN.fullmatch(long)
    assert SLUG_PATTERN.fullmatch(slug_from_name("a-" * 30, "abcd1234"))


def test_a_personal_org_is_named_after_its_person() -> None:
    assert personal_org_name(" Dee ") == "Dee"
    assert personal_org_name("  ") == "Personal"


@pytest.mark.parametrize(
    ("name", "slug"), [("", None), ("  ", "ok"), ("Cafe", "Cafe"), ("Cafe", "b" * 49)]
)
def test_a_team_org_has_a_name_and_a_well_formed_slug(name: str, slug: str | None) -> None:
    with pytest.raises(ValueError):
        check_org(name, slug)


def test_a_team_org_may_leave_its_slug_to_the_name() -> None:
    check_org("Cafe", None)
    check_org("Cafe", "cafe-2")


@pytest.mark.parametrize("name", ["Europe/Istanbul", "America/Argentina/Buenos_Aires", "UTC"])
def test_a_time_zone_is_an_iana_name(name: str) -> None:
    check_time_zone(name)


@pytest.mark.parametrize(
    "name",
    ["+03:00", "GMT+3 ", "Mars/Olympus_Mons", "../../etc/passwd", "/etc/localtime", "x" * 65],
)
def test_anything_else_is_refused_as_a_time_zone(name: str) -> None:
    with pytest.raises(ValueError):
        check_time_zone(name)
