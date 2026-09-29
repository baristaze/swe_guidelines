"""The billing namespace's pure rules: the one table of what each plan
entitles an org to and costs, the plan a mirror derives, and the refusal a
lever makes."""

from datetime import timedelta

import pytest
from contracts.billing_storage import make_account

from acme.om.base import utcnow
from acme.om.billing.impl.manager import billing_of
from acme.om.billing.rules import (
    PLAN_LIMITS,
    PLAN_PRICES,
    effective_plan,
    monthly_price_cents,
    paid_plan,
    refuse_past,
    seats_metered,
    should_mirror,
    suggested_plan,
)
from acme.om.billing.types.account import BillingAccount, SubscriptionStatus, status_of
from acme.om.billing.types.plan import Lever, Plan
from acme.om.exceptions import PlanLimitReached


def paying(
    plan: Plan, status: SubscriptionStatus = SubscriptionStatus.ACTIVE, **fields: object
) -> BillingAccount:
    return make_account(
        **{
            "subscription_id": "sub_1",
            "price_lookup_key": PLAN_PRICES[plan].lookup_key,
            "status": status,
            "current_period_end": utcnow() + timedelta(days=10),
            "quantity": 1,
            **fields,
        }
    )


def test_the_table_holds_the_four_plans() -> None:
    free, pro, team, top = (PLAN_LIMITS[p] for p in (Plan.FREE, Plan.PRO, Plan.TEAM, Plan.MAX))
    assert (free.members, free.api_keys, free.active_tasks) == (1, False, 10)
    assert (pro.members, pro.api_keys, pro.active_tasks) == (1, True, None)
    assert (team.members, team.api_keys, team.active_tasks) == (5, True, None)
    assert (top.members, top.api_keys, top.active_tasks) == (None, True, None)
    sizes = [PLAN_LIMITS[p].storage_bytes for p in (Plan.FREE, Plan.PRO, Plan.TEAM, Plan.MAX)]
    assert sizes == sorted(sizes) and len(set(sizes)) == 4


def test_the_tenth_active_task_is_room_and_the_eleventh_is_refused_on_free() -> None:
    refuse_past(Plan.FREE, Lever.ACTIVE_TASKS, 9)
    with pytest.raises(PlanLimitReached) as refused:
        refuse_past(Plan.FREE, Lever.ACTIVE_TASKS, 10)
    assert refused.value.http_status == 402
    assert refused.value.code == "plan_limit_reached"
    assert (refused.value.plan, refused.value.lever, refused.value.limit) == (
        "free",
        "active_tasks",
        10,
    )
    assert refused.value.suggested_plan == "pro"


def test_pro_has_no_bound_on_active_tasks() -> None:
    refuse_past(Plan.PRO, Lever.ACTIVE_TASKS, 10_000)


@pytest.mark.parametrize(
    ("plan", "seats", "suggested"),
    [(Plan.FREE, 1, "team"), (Plan.PRO, 1, "team"), (Plan.TEAM, 5, "max")],
)
def test_the_member_past_the_seats_is_refused_and_the_plan_that_lifts_it_is_named(
    plan: Plan, seats: int, suggested: str
) -> None:
    refuse_past(plan, Lever.MEMBERS, seats - 1)
    with pytest.raises(PlanLimitReached) as refused:
        refuse_past(plan, Lever.MEMBERS, seats)
    assert refused.value.limit == seats
    assert refused.value.suggested_plan == suggested


def test_max_seats_have_no_bound() -> None:
    refuse_past(Plan.MAX, Lever.MEMBERS, 500)


def test_the_first_api_key_is_refused_on_free_and_allowed_on_every_paid_plan() -> None:
    with pytest.raises(PlanLimitReached) as refused:
        refuse_past(Plan.FREE, Lever.API_KEYS, 0)
    assert (refused.value.limit, refused.value.suggested_plan) == (0, "pro")
    assert refused.value.message == "the free plan includes no api keys"
    for plan in (Plan.PRO, Plan.TEAM, Plan.MAX):
        refuse_past(plan, Lever.API_KEYS, 0)


def test_no_plan_lifts_a_bound_max_already_lifts() -> None:
    assert suggested_plan(Lever.MEMBERS, Plan.MAX, 1000) is None


@pytest.mark.parametrize(
    ("seats", "cents"), [(1, 3000), (9, 3000), (10, 3000), (11, 3300), (12, 3600), (20, 6000)]
)
def test_max_is_thirty_dollars_up_to_ten_seats_and_three_a_seat_past_them(
    seats: int, cents: int
) -> None:
    assert monthly_price_cents(Plan.MAX, seats) == cents


def test_the_flat_plans_ignore_the_seat_count() -> None:
    assert monthly_price_cents(Plan.PRO, 1) == 500
    assert monthly_price_cents(Plan.TEAM, 5) == 1000
    assert monthly_price_cents(Plan.FREE, 3) == 0
    assert [p for p in Plan if seats_metered(p)] == [Plan.MAX]


def test_the_paid_plan_is_the_subscriptions_while_it_is_live() -> None:
    now = utcnow()
    assert paid_plan(None, now) is None
    assert paid_plan(make_account(), now) is None
    assert paid_plan(paying(Plan.PRO), now) is Plan.PRO
    assert paid_plan(paying(Plan.TEAM, SubscriptionStatus.PAST_DUE), now) is Plan.TEAM
    assert paid_plan(paying(Plan.TEAM, SubscriptionStatus.TRIALING), now) is Plan.TEAM
    for gone in (
        SubscriptionStatus.CANCELED,
        SubscriptionStatus.UNPAID,
        SubscriptionStatus.INCOMPLETE,
        SubscriptionStatus.INCOMPLETE_EXPIRED,
        SubscriptionStatus.PAUSED,
    ):
        assert paid_plan(paying(Plan.PRO, gone), now) is None
    assert paid_plan(paying(Plan.PRO, price_lookup_key="someone.else"), now) is None


def test_a_cancelled_plan_holds_until_its_period_ends_then_drops_to_free() -> None:
    account = paying(Plan.PRO, cancel_at_period_end=True)
    assert account.current_period_end is not None
    before = account.current_period_end - timedelta(seconds=1)
    billing = billing_of(account, before)
    assert (billing.plan, billing.ends_at, billing.plan_after) == (
        Plan.PRO,
        account.current_period_end,
        Plan.FREE,
    )
    after = billing_of(account, account.current_period_end)
    assert (after.plan, after.ends_at, after.plan_after) == (Plan.FREE, None, None)


def test_a_grant_and_a_payment_give_the_higher_plan() -> None:
    now = utcnow()
    assert effective_plan(None, now) is Plan.FREE
    assert effective_plan(make_account(None, comped_plan=Plan.TEAM), now) is Plan.TEAM
    assert effective_plan(paying(Plan.PRO, comped_plan=Plan.TEAM), now) is Plan.TEAM
    assert effective_plan(paying(Plan.MAX, comped_plan=Plan.PRO), now) is Plan.MAX
    ending = paying(Plan.MAX, cancel_at_period_end=True, comped_plan=Plan.TEAM)
    assert billing_of(ending, now).plan_after is Plan.TEAM


def test_the_mirror_follows_its_own_subscription_or_a_live_one() -> None:
    followed = paying(Plan.PRO)
    assert should_mirror(None, "sub_x", live=False)
    assert should_mirror(make_account(), "sub_x", live=False)
    assert should_mirror(followed, "sub_1", live=False)
    assert should_mirror(followed, "sub_2", live=True)
    # A late word about an older, ended subscription does not overwrite it.
    assert not should_mirror(followed, "sub_0", live=False)


def test_a_status_this_build_does_not_know_carries_no_plan() -> None:
    assert status_of("active") is SubscriptionStatus.ACTIVE
    assert status_of("something_new") is SubscriptionStatus.INCOMPLETE
