from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Column, Double, Index, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import (
    Base,
    IdentifiableMixin,
    SoftDeletableMixin,
    TrackableMixin,
)
from acme.om.tasks.rules import RANK_SCALE_BOUND


class Tasks(IdentifiableMixin, TrackableMixin, SoftDeletableMixin, Base):
    __tablename__ = "tasks"
    # Two lists per org: the open one in manual order, the done one newest
    # first; and the open one newest first, the short list Slack shows. Each
    # has its compound index, so org_id gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        Index("ix_tasks_org_id_status_rank", "org_id", "status", "rank"),
        # The rank as a float: in the table and out of the mapping. The
        # release before names it in every insert and reads it as a number,
        # so a trigger keeps it the rank's float on every row this release
        # writes (migration 202610200000). No statement of this release names
        # it, and the release after this one drops it and this line (ADR
        # 0038, ADR 0050).
        Column("position", Double(), nullable=False),
        Index("ix_tasks_org_id_status_id", "org_id", "status", "id"),
        # The done list and the archive, one index per shelf, so the cleanup's
        # read of the archivable tasks never walks the archive. The predicates
        # leave `status` out: it is bound as a parameter, and a generic plan
        # cannot prove a partial predicate on one.
        Index(
            "ix_tasks_org_id_status_updated_at_id_unarchived",
            "org_id",
            "status",
            "updated_at",
            "id",
            postgresql_where=text("archived_at IS NULL AND deleted_at IS NULL"),
        ),
        Index(
            "ix_tasks_org_id_status_updated_at_id_archived",
            "org_id",
            "status",
            "updated_at",
            "id",
            postgresql_where=text("archived_at IS NOT NULL AND deleted_at IS NULL"),
        ),
        # The `mine` scope, one index per arm of its OR: the tasks assigned to
        # the caller, and the unassigned ones the caller made.
        Index(
            "ix_tasks_org_id_assignee_id_status",
            "org_id",
            "assignee_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_tasks_org_id_created_by_status",
            "org_id",
            "created_by",
            "status",
            postgresql_where=text("assignee_id IS NULL AND deleted_at IS NULL"),
        ),
        # The sweep's respace finds a tenant's rank that grew too long, and
        # its read of the tenants with a chore due finds the tenants that have
        # one. Only such ranks are in it, so the read that finds none reads
        # nothing. The bound is a literal, the one tasks.rules.RANK_SCALE_BOUND
        # names.
        Index(
            "ix_tasks_org_id_rank_long",
            "org_id",
            "rank",
            postgresql_where=text(f"scale(rank) > {RANK_SCALE_BOUND} AND deleted_at IS NULL"),
        ),
        # The sweep reads the deleted ones by their delete, across tenants;
        # only they are in it.
        Index("ix_tasks_deleted_at", "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")),
        # The sweep's read of the tenants with a chore due, across tenants:
        # the done shelf of every tenant by its last change, so the day's
        # cleanup cut is a range of it and only the archivable tasks are read.
        # The status is a literal, the one the read names, so a generic plan
        # proves the predicate; a write that leaves a task open, archived, or
        # deleted writes it no entry.
        Index(
            "ix_tasks_updated_at_org_id_done_unarchived",
            "updated_at",
            "org_id",
            postgresql_where=text("status = 'done' AND archived_at IS NULL AND deleted_at IS NULL"),
        ),
    )
    __mapper_args__ = {"exclude_properties": ["position"]}
    title: Mapped[str]
    notes: Mapped[str]
    status: Mapped[str]
    assignee_id: Mapped[UUID | None]
    # The order of the open list: an exact decimal, compared exactly
    # (tasks.rules.spread).
    rank: Mapped[Decimal] = mapped_column(Numeric())
    # The compare-and-set column. The server default is for a row an older
    # build inserts during a rollout; the object model always sends a value.
    version: Mapped[int] = mapped_column(server_default="1")
    due_on: Mapped[date | None]
    reminded_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]
