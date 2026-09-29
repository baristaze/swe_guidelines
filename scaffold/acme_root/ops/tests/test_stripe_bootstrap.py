"""The bootstrap over the catalog twin and a secret store in memory: a first
run makes the account whole, a second changes nothing, a changed amount is
a new price that takes the lookup key, a lost endpoint secret rolls the
endpoint, the bootstrap reads its own key and refuses one that is not a
restricted key of the environment's mode, and the committed tiers price
seats the way the object model's rule does."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from acme.integrations.payments.catalog import CatalogTier, CatalogTwinImpl
from acme.om.billing.rules import PLAN_PRICES, monthly_price_cents
from acme.om.billing.types.plan import Plan
from acme.ops.main import main
from acme.ops.stripe_bootstrap import (
    DesiredState,
    SecretStoreInterface,
    SecretStoreNoneImpl,
    check_key,
    load_desired,
    reconcile,
    webhook_secret_name,
)

ROOT = Path(__file__).resolve().parents[2]


class MemorySecretStore(SecretStoreInterface):
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def describe(self) -> str:
        return "memory"

    async def holds(self, name: str) -> bool | None:
        value = self.values.get(name, "")
        return bool(value) and value != "off"

    async def put(self, name: str, value: SecretStr) -> None:
        self.values[name] = value.get_secret_value()


@pytest.fixture
def desired() -> DesiredState:
    return load_desired(ROOT)


async def test_a_first_run_creates_everything_and_a_second_changes_nothing(
    desired: DesiredState,
) -> None:
    catalog, store = CatalogTwinImpl(), MemorySecretStore()
    first = await reconcile(desired, "staging", catalog, store)
    # Three products, three prices, the portal configuration, the endpoint.
    assert first.count("created") == 8
    assert len(catalog.writes) == 8
    assert store.values.keys() == {webhook_secret_name("staging")}

    writes = len(catalog.writes)
    second = await reconcile(desired, "staging", catalog, store)
    assert second.changes == 0
    assert second.count("unchanged") == 8
    assert len(catalog.writes) == writes
    assert second.summary().startswith("no changes")


async def test_the_secret_is_stored_and_never_printed(desired: DesiredState) -> None:
    catalog, store = CatalogTwinImpl(), MemorySecretStore()
    run = await reconcile(desired, "staging", catalog, store)
    secret = store.values[webhook_secret_name("staging")]
    assert secret.startswith("whsec_")
    assert all(secret not in line for line in run.lines())


async def test_a_changed_amount_is_a_new_price_that_takes_the_lookup_key(
    desired: DesiredState,
) -> None:
    catalog, store = CatalogTwinImpl(), MemorySecretStore()
    await reconcile(desired, "staging", catalog, store)
    old = next(p for p in catalog.prices.values() if p.lookup_key == "acme.pro.monthly")

    raised = desired.model_copy(
        update={
            "prices": tuple(
                p.model_copy(update={"unit_amount": 700})
                if p.lookup_key == "acme.pro.monthly"
                else p
                for p in desired.prices
            )
        }
    )
    run = await reconcile(raised, "staging", catalog, store)

    holders = [p for p in catalog.prices.values() if p.lookup_key == "acme.pro.monthly"]
    assert len(holders) == 1 and holders[0].id != old.id and holders[0].unit_amount == 700
    assert catalog.prices[old.id].active is False and catalog.prices[old.id].lookup_key is None
    assert run.count("created") == 1 and run.count("archived") == 1
    # The portal offers the price that now holds the key.
    assert run.count("updated") == 1
    assert (await reconcile(raised, "staging", catalog, store)).changes == 0


async def test_a_lost_secret_rolls_the_endpoint(desired: DesiredState) -> None:
    catalog, store = CatalogTwinImpl(), MemorySecretStore()
    await reconcile(desired, "staging", catalog, store)
    (before,) = catalog.endpoints
    first_secret = store.values[webhook_secret_name("staging")]
    store.values[webhook_secret_name("staging")] = "off"

    run = await reconcile(desired, "staging", catalog, store)

    (after,) = catalog.endpoints
    assert after != before
    assert run.count("rolled") == 1
    assert store.values[webhook_secret_name("staging")] not in ("off", first_secret)


async def test_without_a_store_the_endpoint_is_kept_and_reruns_stay_still(
    desired: DesiredState,
) -> None:
    catalog = CatalogTwinImpl()
    first = await reconcile(desired, "staging", catalog, SecretStoreNoneImpl())
    assert first.count("created") == 8
    second = await reconcile(desired, "staging", catalog, SecretStoreNoneImpl())
    assert second.changes == 0
    assert any("not held here" in note for note in second.notes)


async def test_local_has_no_endpoint_and_points_at_stripe_listen(desired: DesiredState) -> None:
    catalog = CatalogTwinImpl()
    run = await reconcile(desired, "local", catalog, SecretStoreNoneImpl())
    assert not catalog.endpoints
    assert any("stripe listen" in note for note in run.notes)


async def test_a_dry_run_writes_nothing(desired: DesiredState) -> None:
    catalog = CatalogTwinImpl()
    run = await reconcile(desired, "staging", catalog, MemorySecretStore(), dry_run=True)
    assert run.count("created") == 8
    assert catalog.writes == []


@pytest.mark.parametrize(
    ("env", "key"),
    [
        ("staging", "rk_live_x"),
        ("local", "rk_live_x"),
        ("production", "rk_test_x"),
        ("staging", "pk_test_x"),
    ],
)
def test_a_key_of_the_wrong_mode_is_refused(env: str, key: str) -> None:
    with pytest.raises(ValueError, match="takes"):
        check_key(env, key)


@pytest.mark.parametrize(
    ("env", "key", "said"),
    [
        ("staging", "rk_org_test_x", "organization key"),
        ("production", "sk_org_live_x", "organization key"),
        ("staging", "sk_test_x", "secret key"),
        ("production", "sk_live_x", "secret key"),
    ],
)
def test_only_a_restricted_key_is_taken(env: str, key: str, said: str) -> None:
    with pytest.raises(ValueError, match=said) as refusal:
        check_key(env, key)
    assert key not in str(refusal.value)


def test_a_restricted_key_of_the_right_mode_passes() -> None:
    check_key("staging", "rk_test_x")
    check_key("production", "rk_live_x")


def test_the_command_reads_its_own_key_and_never_the_runtime_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The runtime key in the shell is not a bootstrap key: the command asks
    for its own and touches nothing."""
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("ACME_STRIPE_BOOTSTRAP_KEY", raising=False)
    monkeypatch.setenv("ACME_STRIPE_RUNTIME_KEY", "rk_test_not_a_real_key")
    assert main(["stripe-bootstrap", "--env", "staging", "--secret-store", "none"]) == 2
    err = capsys.readouterr().err
    assert "set ACME_STRIPE_BOOTSTRAP_KEY" in err
    assert "not_a_real_key" not in err


def test_the_command_refuses_a_live_key_on_staging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("ACME_STRIPE_BOOTSTRAP_KEY", "rk_live_not_a_real_key")
    assert main(["stripe-bootstrap", "--env", "staging", "--secret-store", "none"]) == 2
    err = capsys.readouterr().err
    assert "takes a test key" in err
    assert "not_a_real_key" not in err


def test_the_command_refuses_to_write_the_secret_under_an_investigate_profile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The signing secret is written under a person's own sign-in; an agent's
    read-only profile is refused before anything reaches the processor."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("ACME_STRIPE_BOOTSTRAP_KEY", "rk_test_not_a_real_key")
    argv = ["stripe-bootstrap", "--env", "staging", "--profile", "acme-staging-investigate"]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "writes no secret" in err
    assert "not_a_real_key" not in err


def _billed(tiers: tuple[CatalogTier, ...], seats: int) -> int:
    """A volume tier prices the whole count at the tier the count reaches."""
    for tier in tiers:
        if tier.up_to is None or seats <= tier.up_to:
            return (tier.flat_amount or 0) + (tier.unit_amount or 0) * seats
    raise AssertionError("no tier takes the count")


@pytest.mark.parametrize("seats", [1, 10, 11, 25])
def test_the_max_tiers_price_seats_as_the_object_model_does(
    desired: DesiredState, seats: int
) -> None:
    (max_price,) = (p for p in desired.prices if p.lookup_key == "acme.max.monthly")
    spec = desired.spec(max_price)
    assert spec.tiers_mode == "volume"
    assert _billed(spec.tiers, seats) == monthly_price_cents(Plan.MAX, seats)


def test_ten_seats_are_thirty_dollars_and_eleven_are_thirty_three(desired: DesiredState) -> None:
    (max_price,) = (p for p in desired.prices if p.lookup_key == "acme.max.monthly")
    tiers = desired.spec(max_price).tiers
    assert _billed(tiers, 10) == 3000 == monthly_price_cents(Plan.MAX, 10)
    assert _billed(tiers, 11) == 3300 == monthly_price_cents(Plan.MAX, 11)


def test_the_lookup_keys_and_flat_amounts_are_the_object_models(desired: DesiredState) -> None:
    by_key = {p.lookup_key: p for p in desired.prices}
    paid = {plan: price for plan, price in PLAN_PRICES.items() if price.lookup_key}
    assert set(by_key) == {price.lookup_key for price in paid.values()}
    for price in paid.values():
        assert price.lookup_key is not None
        spec = desired.spec(by_key[price.lookup_key])
        if spec.tiers:
            assert spec.tiers[0].flat_amount == price.flat_cents
            assert spec.tiers[0].up_to == price.included_seats
            assert spec.tiers[-1].unit_amount == price.per_seat_cents
        else:
            assert spec.unit_amount == price.flat_cents
