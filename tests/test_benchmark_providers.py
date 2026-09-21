"""benchmark/harness/providers.py: the selection flag and the keys."""

import pytest

from harness import providers as P


def test_a_number_and_a_list_of_names_are_the_same_selection():
    assert P.parse("3") == P.parse("anthropic,openai")
    assert P.parse(7) == P.Provider.ANTHROPIC | P.Provider.OPENAI | P.Provider.GEMINI
    assert P.parse("all") == P.ALL
    assert P.parse(None) == P.ALL


def test_members_come_back_in_flag_order():
    assert [P.name(p) for p in P.members(P.parse("15"))] == ["anthropic", "openai", "gemini", "xai"]


def test_an_unknown_name_is_refused():
    with pytest.raises(ValueError, match="unknown provider"):
        P.parse("anthropic,acme")


def test_a_bit_no_provider_owns_is_refused():
    with pytest.raises(ValueError, match="no provider owns"):
        P.parse("16")
    with pytest.raises(ValueError, match="positive"):
        P.parse("0")


def test_a_key_is_read_from_the_environment_given():
    env = {"ANTHROPIC_API_KEY": "a"}
    assert P.key(P.Provider.ANTHROPIC, env) == "a"
    assert P.available(P.Provider.ANTHROPIC, env)
    assert not P.available(P.Provider.OPENAI, env)


def test_xai_falls_back_to_the_second_key_name():
    assert P.key(P.Provider.XAI, {"GROK_API_KEY": "g"}) == "g"
    assert P.key(P.Provider.XAI, {"XAI_API_KEY": "x", "GROK_API_KEY": "g"}) == "x"


def test_gemini_never_reads_the_google_key():
    assert P.key(P.Provider.GEMINI, {"GOOGLE_API_KEY": "wrong-account"}) is None


def test_availability_names_the_keys_it_looked_for():
    rows = P.availability(P.parse("8"), {})
    assert rows == [("xai", False, "XAI_API_KEY or GROK_API_KEY")]


def test_a_selection_of_more_than_one_has_no_single_name():
    with pytest.raises(ValueError, match="not a single provider"):
        P.name(P.ALL)
    with pytest.raises(ValueError, match="not a single provider"):
        P.name(P.parse("3"))
