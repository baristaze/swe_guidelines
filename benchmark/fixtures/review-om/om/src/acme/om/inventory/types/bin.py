from datetime import datetime
from uuid import UUID

from acme.om.base import Identifiable, Named, Trackable

NO_BIN = UUID(int=0)


class Bin(Identifiable, Named, Trackable):
    warehouse_id: UUID
    parent_bin_id: UUID = NO_BIN
    created_at: datetime
