"""The two restricted keys Acme holds for the payment processor, and what
each may touch, named as Stripe's restricted-key editor names them.

The runtime key serves the API and the worker, from the environment's
secret store. The bootstrap key is held by the person who runs
`acme-ops stripe-bootstrap`, from their shell, and never reaches the
cloud. Every other resource of either key stays None.
docs/runbooks/providers/stripe.md carries the same two tables for the
person who makes the keys."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Permission:
    resource: str
    """The resource as the dashboard's restricted-key editor names it."""
    group: str
    """The group the editor lists the resource under, in its left column."""
    level: Literal["Read", "Write"]


CUSTOMERS = Permission("Customers", "Core", "Write")
CHECKOUT_SESSIONS = Permission("Checkout Sessions", "Checkout Sessions", "Write")
CUSTOMER_PORTAL = Permission("Customer Portal", "Billing", "Write")
SUBSCRIPTIONS = Permission("Subscriptions", "Billing", "Write")
PRICES_READ = Permission("Prices", "Billing", "Read")

PRODUCTS = Permission("Products", "Core", "Write")
PRICES_WRITE = Permission("Prices", "Billing", "Write")
WEBHOOK_ENDPOINTS = Permission(
    "Webhook Endpoints, Event Destinations", "Webhook Endpoints", "Write"
)

RUNTIME_PERMISSIONS = (CUSTOMERS, CHECKOUT_SESSIONS, CUSTOMER_PORTAL, SUBSCRIPTIONS, PRICES_READ)
"""The API and the worker: a customer per org (create, read, and delete
when its account or its team org is deleted), a hosted checkout, a portal session and the
list of portal configurations, a subscription read, changed, and canceled,
and a price found by lookup key."""

BOOTSTRAP_PERMISSIONS = (PRODUCTS, PRICES_WRITE, WEBHOOK_ENDPOINTS, CUSTOMER_PORTAL)
"""`acme-ops stripe-bootstrap`: the products, the prices, the environment's
webhook endpoint, and the portal configuration, each listed, created, and
updated; a price archived; an endpoint deleted when it is rolled."""
