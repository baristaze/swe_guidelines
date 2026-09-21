from uuid import UUID

from acme.om.base import Identifiable, Trackable


class StockMovement(Identifiable, Trackable):
    """One line of the stock ledger. Written once, never changed."""

    bin_id: UUID
    quantity: int
