"""The placement rule, the same cases the portal's stream.test.ts pins."""

from acme.client.stream import Gap, Next, Seen, behind, is_last_page, place


def test_the_first_push_sets_the_cursor() -> None:
    assert place(None, 5) == Next("next", 5)


def test_the_next_seq_advances() -> None:
    assert place(7, 8) == Next("next", 8)


def test_a_seq_at_or_behind_the_cursor_is_seen() -> None:
    assert place(7, 7) == Seen("seen")
    assert place(7, 3) == Seen("seen")


def test_a_skipped_seq_names_the_replay_point() -> None:
    assert place(7, 9) == Gap("gap", 7)
    assert place(7, 100) == Gap("gap", 7)


def test_a_head_past_the_cursor_names_the_replay_point() -> None:
    assert behind(7, 9) == 7
    assert behind(7, 8) == 7


def test_a_head_at_or_behind_the_cursor_is_quiet_and_so_is_no_cursor() -> None:
    assert behind(7, 7) is None
    assert behind(7, 3) is None
    assert behind(None, 9) is None


def test_paging_ends_on_a_short_page() -> None:
    assert is_last_page(200, 200) is False
    assert is_last_page(12, 200) is True
