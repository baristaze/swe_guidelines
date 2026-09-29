import random
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from contracts.task_storage import make_task

from acme.om.base import new_id, utcnow
from acme.om.tasks.rules import (
    RANK_SCALE_BOUND,
    RANK_SCALE_SHORT,
    RESPACE_REACH,
    Place,
    archive_cutoff,
    bulk_ranks,
    bulk_skip,
    cleanup_period,
    earliest_reminder_time,
    is_after,
    is_before,
    is_short,
    is_visible,
    needs_respace,
    rank_after,
    rank_between,
    rank_scale,
    reminder_person,
    reminder_time,
    respace_run,
    respaced,
    spread,
    top_rank,
)
from acme.om.tasks.types.bulk import BulkAction, SkipReason
from acme.om.tasks.types.filter import OpenTaskCursor, TaskCursor, TaskFilter
from acme.om.tasks.types.task import TaskScope, TaskStatus


def mine(user_id: UUID) -> TaskFilter:
    return TaskFilter(scope=TaskScope.MINE, user_id=user_id)


def test_mine_is_assigned_to_me_or_unassigned_and_created_by_me() -> None:
    me, other = new_id(), new_id()
    assert is_visible(make_task(created_by=me), mine(me))
    assert is_visible(make_task(created_by=other, assignee_id=me), mine(me))
    assert not is_visible(make_task(created_by=me, assignee_id=other), mine(me))
    assert not is_visible(make_task(created_by=other), mine(me))
    assert is_visible(make_task(created_by=other), TaskFilter(scope=TaskScope.TEAM, user_id=me))


def test_the_cursor_cuts_strictly_before_by_updated_at_then_id() -> None:
    task = make_task()
    at = TaskCursor(updated_at=task.updated_at, id=task.id)
    assert not is_before(task, at)
    assert is_before(task, TaskCursor(updated_at=task.updated_at, id=UUID(int=task.id.int + 1)))
    assert not is_before(task, TaskCursor(updated_at=task.updated_at, id=UUID(int=task.id.int - 1)))


# Three ids in ascending order, so a place is written as (rank, id) and two
# places that share a rank are told apart the way the list tells them.
A, B, C = (UUID(int=n) for n in (10, 20, 30))


def D(value: str | int) -> Decimal:  # noqa: N802 - a literal, read as one
    return Decimal(str(value))


def random_rank(rng: random.Random) -> Decimal:
    """A rank as the rules or the fill write one: a whole number, or a few
    digits after the point, negative too."""
    scale = rng.choice([0, 0, 1, 2, 5, 12, 20])
    return spread(None, None, 1)[0] + Decimal(rng.randint(-(10**6), 10**6)).scaleb(-scale)


def test_ranks_between_ends_and_neighbours() -> None:
    assert spread(None, None, 3) == [D(0), D(1), D(2)]
    assert top_rank([]) == D(0)
    assert top_rank([(D(-1), A), (D(2), B)]) == D(-2)
    assert top_rank([(D("-1.5"), A)]) == D(-3), "a whole number, below by at least one"
    assert rank_after(D(1), D(2)) == D("1.5")
    assert rank_after(D(1), D(3)) == D(2), "the shortest there is"
    assert rank_after(D(2), None) == D(3)
    assert rank_after(D("0.1"), D("0.2")) == D("0.15")
    assert rank_after(D(0), D("0.5")) == D("0.2")
    assert spread(D(0), D(1), 9) == [D(f"0.{n}") for n in range(1, 10)]
    with pytest.raises(ValueError, match="between"):
        rank_between(D(1), D(1))


def test_a_rank_is_written_without_trailing_zeros_or_an_exponent() -> None:
    for rank in [*spread(D(0), D(1), 20), rank_between(D(0), D("0.0000001"))]:
        _, digits, exponent = rank.as_tuple()
        assert isinstance(exponent, int) and (exponent >= 0 or digits[-1] != 0)
        assert "E" not in format(rank, "f")
        assert rank_scale(rank) == max(0, -exponent)
    assert rank_scale(D("1.500")) == 1 and rank_scale(D(100)) == 0
    # Past the twenty-eight digits of Python's default context, every digit
    # still counts.
    assert rank_scale(D("-1.999999999999999999999999999998")) == 30


def test_between_any_two_ranks_there_is_one_and_it_is_short() -> None:
    """The property a move leans on: between two different ranks there is
    always another, strictly between, at most one digit longer than the
    longer of the two. So a move writes its one row and never runs out of
    room."""
    rng = random.Random(81)
    for _ in range(5000):
        low, high = sorted({random_rank(rng), random_rank(rng)} | {random_rank(rng)})[:2]
        if low == high:
            continue
        rank = rank_between(low, high)
        assert low < rank < high
        assert rank_scale(rank) <= max(rank_scale(low), rank_scale(high)) + 1
        count = rng.randint(1, 50)
        ranks = spread(low, high, count)
        assert len(ranks) == count
        assert all(a < b for a, b in zip([low, *ranks], [*ranks, high], strict=True))
        assert rank_scale(ranks[-1]) <= max(rank_scale(low), rank_scale(high)) + 3


def test_prepending_and_appending_stay_whole_numbers() -> None:
    """A create goes on top and an import at the bottom, every time: the
    ranks stay whole numbers however many there are."""
    places: list[Place] = []
    for _ in range(1000):
        places.insert(0, (top_rank(places), new_id()))
    assert all(rank_scale(rank) == 0 for rank, _ in places)
    assert [rank for rank, _ in places] == sorted(rank for rank, _ in places)
    bottom = places[-1][0]
    appended = spread(bottom, None, 1000)
    assert appended[0] > bottom and appended == sorted(set(appended))
    assert all(rank_scale(rank) == 0 for rank in appended)


def following_rank(
    order: list[tuple[Decimal, UUID]], task_id: UUID, anchor: Place
) -> Decimal | None:
    """What the manager reads for a move: the smallest rank past the
    anchor's, the moved task aside."""
    past = [rank for rank, other in order if other != task_id and rank > anchor[0]]
    return min(past) if past else None


def test_many_moves_keep_the_order_they_asked_for() -> None:
    """Random moves over a list, each placed by the one neighbour a move
    reads: after every move the list sorted by (rank, id) is the list the
    moves asked for, and each move changed one rank."""
    rng = random.Random(7)
    for _ in range(40):
        ids = [new_id() for _ in range(rng.randint(2, 30))]
        ranks = dict(zip(ids, spread(None, None, len(ids)), strict=True))
        wanted = list(ids)
        for _ in range(300):
            task_id = rng.choice(wanted)
            rest = [other for other in wanted if other != task_id]
            anchor_id = rng.choice([None, *rest])
            order = sorted((rank, other) for other, rank in ranks.items())
            if anchor_id is None:
                new = top_rank([place for place in order if place[1] != task_id])
                wanted = [task_id, *rest]
            else:
                anchor = (ranks[anchor_id], anchor_id)
                new = rank_after(anchor[0], following_rank(order, task_id, anchor))
                at = rest.index(anchor_id) + 1
                wanted = [*rest[:at], task_id, *rest[at:]]
            before = dict(ranks)
            ranks[task_id] = new
            assert [other for other in ids if ranks[other] != before[other]] in ([], [task_id])
            assert [other for _, other in sorted((r, o) for o, r in ranks.items())] == wanted


def test_moves_into_one_gap_grow_the_rank_slowly() -> None:
    """The worst case for a move: every move lands right after the same task,
    so every rank halves the same gap. A digit comes every three moves or so,
    and it takes seventy-odd moves to pass the bound the sweep respaces at.
    A float ran out after fifty-odd, and every open task was renumbered."""
    anchor, following = D(0), D(1)
    moves = 0
    while not needs_respace(following):
        following = rank_after(anchor, following)
        moves += 1
    assert 70 <= moves <= 80
    assert rank_after(anchor, following) > anchor, "and there is still room"


def test_a_move_after_a_task_that_shares_its_rank_goes_after_both() -> None:
    """Two open tasks can share a rank: two creates that read the same top.
    The list orders them by id. The rank past the anchor's is the one a move
    places before, so a task moved after either twin lands after both, never
    on their rank."""
    order = [(D(5), A), (D(5), B), (D(6), C)]
    moved = new_id()
    rank = rank_after(D(5), following_rank(order, moved, (D(5), A)))
    assert D(5) < rank < D(6)
    assert [task for _, task in sorted([*order, (rank, moved)])] == [A, B, moved, C]


def test_is_after_cuts_the_open_list_by_rank_then_id() -> None:
    task = make_task(rank=2)
    at = OpenTaskCursor(rank=task.rank, id=task.id)
    assert not is_after(task, at), "the cursor's own task is on the previous page"
    assert is_after(task, OpenTaskCursor(rank=D(1), id=task.id))
    assert not is_after(task, OpenTaskCursor(rank=D(3), id=task.id))
    assert is_after(task, OpenTaskCursor(rank=D(2), id=UUID(int=task.id.int - 1)))
    assert not is_after(task, OpenTaskCursor(rank=D(2), id=UUID(int=task.id.int + 1)))
    assert is_after(make_task(rank="2.0000000000000000000001"), at), "every digit counts"


def chain(length: int) -> list[Place]:
    """The places a run of moves into one gap leaves: zero, then `length`
    tasks each moved right after it, then one."""
    places: list[Place] = [(D(0), A)]
    following = D(1)
    for _ in range(length):
        following = rank_after(D(0), following)
        places.append((following, new_id()))
    places.append((D(1), C))
    return sorted(places)


def test_the_run_is_bounded_by_the_nearest_short_ranks() -> None:
    places = chain(90)
    long = next(place for place in places if needs_respace(place[0]))
    at = places.index(long)
    above = list(reversed(places[:at]))
    below = places[at + 1 :]
    run = respace_run(long, above, below, RESPACE_REACH)
    assert run.low == D(0) and run.high is not None and is_short(run.high)
    assert long in run.places
    assert all(not is_short(rank) for rank, _ in run.places)
    first = places.index(run.places[0])
    assert list(run.places) == places[first : first + len(run.places)], "contiguous, in order"
    ranks = respaced(run)
    assert len(ranks) == len(run.places)
    assert all(D(0) < rank < run.high for rank in ranks)
    assert ranks == sorted(set(ranks))
    assert all(
        not needs_respace(rank) and rank_scale(rank) <= RANK_SCALE_SHORT + 3 for rank in ranks
    )


def test_a_run_at_an_end_of_the_list_is_open_there() -> None:
    long = (D("0." + "0" * RANK_SCALE_BOUND + "1"), A)
    run = respace_run(long, [], [], RESPACE_REACH)
    assert run.low is None and run.high is None and respaced(run) == [D(0)]
    tail = [(long[0] * 2, B)]
    run = respace_run(long, [(D(-1), C)], tail, RESPACE_REACH)
    assert run.low == D(-1) and run.high is None
    assert respaced(run) == [D(0), D(1)], "whole numbers after the last short one"


def test_a_run_stops_at_the_reach() -> None:
    """With no short rank within the reach, the farthest place read bounds
    the run, so one respace writes at most twice the reach and one."""
    long_ranks = spread(D(0), D("0." + "0" * 30 + "1"), 7)
    places = [(rank, new_id()) for rank in long_ranks]
    run = respace_run(places[3], list(reversed(places[:3])), places[4:], 3)
    assert run.low == places[0][0] and run.high == places[6][0]
    assert list(run.places) == places[1:6]


def test_respacing_keeps_the_order_whatever_the_list() -> None:
    """Random lists with runs of long ranks: applying one respace changes no
    task's place in the list and leaves no rank past the bound in the run."""
    rng = random.Random(50)
    for _ in range(300):
        places = chain(rng.randint(80, 150))
        extra = [(random_rank(rng), new_id()) for _ in range(rng.randint(0, 20))]
        order = sorted(set(places) | set(extra))
        long = next((p for p in order if needs_respace(p[0])), None)
        assert long is not None
        at = order.index(long)
        reach = rng.choice([RESPACE_REACH, 5, 40])
        above = list(reversed(order[:at]))[:reach]
        below = order[at + 1 :][:reach]
        run = respace_run(long, above, below, reach)
        new = dict(zip([task for _, task in run.places], respaced(run), strict=True))
        after = sorted((new.get(task, rank), task) for rank, task in order)
        assert [task for _, task in after] == [task for _, task in order]
        if reach == RESPACE_REACH:
            assert not any(needs_respace(rank) for rank in new.values())


# A due date's reminder: nine in the morning, in the person's time zone.


@pytest.mark.parametrize(
    ("zone", "expected"),
    [
        # Ahead of UTC: the morning comes before UTC's, the day before at +14.
        ("Pacific/Kiritimati", datetime(2030, 9, 29, 19, tzinfo=UTC)),
        ("Europe/Istanbul", datetime(2030, 9, 30, 6, tzinfo=UTC)),
        ("Asia/Kolkata", datetime(2030, 9, 30, 3, 30, tzinfo=UTC)),
        # UTC, and the fallbacks to it.
        ("UTC", datetime(2030, 9, 30, 9, tzinfo=UTC)),
        (None, datetime(2030, 9, 30, 9, tzinfo=UTC)),
        ("Not/A_Zone", datetime(2030, 9, 30, 9, tzinfo=UTC)),
        # Behind UTC: after UTC's morning, in its own summer time.
        ("America/New_York", datetime(2030, 9, 30, 13, tzinfo=UTC)),
        ("Pacific/Honolulu", datetime(2030, 9, 30, 19, tzinfo=UTC)),
    ],
)
def test_the_reminder_is_nine_in_the_morning_of_the_due_date_in_the_zone(
    zone: str | None, expected: datetime
) -> None:
    due = date(2030, 9, 30)
    assert reminder_time(due, zone) == expected
    assert reminder_time(due, zone) >= earliest_reminder_time(due)


def test_daylight_saving_moves_the_utc_hour_and_never_the_local_one() -> None:
    assert reminder_time(date(2030, 1, 15), "Europe/Berlin").hour == 8  # UTC+1
    assert reminder_time(date(2030, 7, 15), "Europe/Berlin").hour == 7  # UTC+2


def test_the_first_morning_of_a_date_is_nine_at_utc_plus_fourteen() -> None:
    assert earliest_reminder_time(date(2030, 9, 30)) == datetime(2030, 9, 29, 19, tzinfo=UTC)


def test_the_reminder_keeps_the_assignees_morning_or_the_creators() -> None:
    creator, assignee = new_id(), new_id()
    assert reminder_person(make_task(created_by=creator)) == creator
    assert reminder_person(make_task(created_by=creator, assignee_id=assignee)) == assignee


def test_a_bulk_change_takes_a_task_only_from_the_status_it_starts_from() -> None:
    open_task, done = make_task(), make_task(status=TaskStatus.DONE)
    gone = make_task().model_copy(update={"deleted_at": utcnow()})
    assert bulk_skip(open_task, BulkAction.COMPLETE) is None
    assert bulk_skip(done, BulkAction.REOPEN) is None
    assert bulk_skip(done, BulkAction.COMPLETE) is SkipReason.ALREADY_DONE
    assert bulk_skip(open_task, BulkAction.REOPEN) is SkipReason.ALREADY_OPEN
    assert bulk_skip(gone, BulkAction.COMPLETE) is SkipReason.NOT_FOUND
    assert bulk_skip(None, BulkAction.REOPEN) is SkipReason.NOT_FOUND


def test_a_bulk_reopen_stacks_each_task_above_the_one_before() -> None:
    assert bulk_ranks(D(0), 3) == [D(-1), D(-2), D(-3)]
    assert bulk_ranks(D("-0.5"), 2) == [D(-2), D(-3)]
    assert bulk_ranks(None, 2) == [D(1), D(0)]
    assert bulk_ranks(D(4), 0) == []


def test_the_days_archive_cut_holds_from_the_start_of_the_utc_day() -> None:
    """Every moment of one UTC day gives the same cut, the day's start less the
    archive age, and the day `cleanup_period` names is that day, whatever the
    moment's own zone."""
    age = timedelta(days=90)
    start = datetime(2030, 5, 20, tzinfo=UTC)
    for moment in (start, start + timedelta(hours=13), start + timedelta(days=1, microseconds=-1)):
        assert archive_cutoff(moment, age) == start - age
        assert cleanup_period(moment) == "2030-05-20"
    east = datetime(2030, 5, 21, 1, tzinfo=timezone(timedelta(hours=3)))  # 22:00 UTC the 20th
    assert archive_cutoff(east, age) == start - age
    assert archive_cutoff(start + timedelta(days=1), age) == start + timedelta(days=1) - age
