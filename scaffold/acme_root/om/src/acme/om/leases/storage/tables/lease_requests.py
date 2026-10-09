from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class LeaseRequests(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "lease_requests"
    __org_id_index__ = False
    __table_args__ = (
        # An ask asked again by its key meets this.
        Index("uq_lease_requests_org_id_idempotency_key", "org_id", "idempotency_key", unique=True),
        # The org's lines of a kind, in rank order.
        Index(
            "ix_lease_requests_org_id_kind_rank_id_waiting",
            "org_id",
            "kind",
            "rank",
            "id",
            postgresql_where=text("status = 'waiting'"),
        ),
        # The ones past their wait, in the org and across tenants.
        Index(
            "ix_lease_requests_org_id_wait_until_waiting",
            "org_id",
            "wait_until",
            postgresql_where=text("status = 'waiting'"),
        ),
        Index(
            "ix_lease_requests_wait_until_waiting",
            "wait_until",
            postgresql_where=text("status = 'waiting'"),
        ),
        # A waiter's requests, which leave every line when it ends; and a
        # resource's by name, which leave when it is retired.
        Index(
            "ix_lease_requests_org_id_waiter_kind_waiter_id_waiting",
            "org_id",
            "waiter_kind",
            "waiter_id",
            postgresql_where=text("status = 'waiting'"),
        ),
        Index(
            "ix_lease_requests_org_id_resource_id_waiting",
            "org_id",
            "resource_id",
            postgresql_where=text("status = 'waiting'"),
        ),
        # The settled ones the purge takes, across tenants, by their last change.
        Index(
            "ix_lease_requests_updated_at_settled",
            "updated_at",
            postgresql_where=text("status <> 'waiting'"),
        ),
    )
    idempotency_key: Mapped[UUID]
    kind: Mapped[str]
    resource_id: Mapped[UUID | None]
    labels: Mapped[list[str] | None]
    payload: Mapped[dict[str, Any]]
    waiter_kind: Mapped[str | None]
    waiter_id: Mapped[UUID | None]
    term_seconds: Mapped[int]
    start_seconds: Mapped[int | None]
    wait_seconds: Mapped[int]
    wait_until: Mapped[datetime]
    rank: Mapped[float]
    status: Mapped[str]
    end_reason: Mapped[str | None]
    lease_id: Mapped[UUID | None]
