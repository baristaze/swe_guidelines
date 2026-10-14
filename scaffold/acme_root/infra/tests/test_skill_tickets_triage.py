"""`tickets-triage` proposes what to close, and closes nothing on its
own."""

from skills_support import prose_of

NAME = "tickets-triage"


def test_triage_closes_nothing_without_the_persons_word() -> None:
    text = prose_of(NAME)
    assert "Never closes a ticket without `--apply` and the person's word in this session" in text
