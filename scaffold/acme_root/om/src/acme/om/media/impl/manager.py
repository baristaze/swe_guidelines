from datetime import timedelta
from itertools import accumulate
from uuid import UUID

from acme.infra.buckets import Buckets, BucketsInterface, part_sizes
from acme.infra.exceptions import InfraException
from acme.infra.flags import Flag, FlagsInterface
from acme.om.base import OmBase, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import (
    FeatureOff,
    NotAuthorized,
    NotFound,
    TenantMismatch,
    ValidationFailed,
)
from acme.om.media.manager import MediaManagerInterface
from acme.om.media.rules import content_disposition, extension_of, object_key, upload_refusal
from acme.om.media.storage import MediaStorageInterface
from acme.om.media.types.file import File, FilePurpose, FileStatus
from acme.om.media.types.page import FilePage
from acme.om.media.types.transfer import DownloadLink, IssuedPart, IssuedUpload
from acme.om.media.types.usage import StorageUsage
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import outbox_row
from acme.om.tenancy import TenancyManagerInterface

BUCKET = Buckets.USER_FILE_UPLOADS
"""Every file of every purpose lives in the one uploads bucket, under the
tenant's prefix and then `media/<purpose>/`."""


class MediaOptions(OmBase):
    max_limit: int = 200
    # A part's URL lives long enough for a slow link to send the window of
    # parts it came with, and a link long enough for a download to start;
    # neither longer. A part whose URL expired is signed again on the next ask.
    upload_ttl: timedelta = timedelta(minutes=15)
    download_ttl: timedelta = timedelta(minutes=5)
    # The most parts one ask signs: the answer stays small whatever the
    # file's size, and the uploader asks again for the rest.
    parts_per_issue: int = 100
    # An upload started and not confirmed by then is abandoned: the sweep
    # removes whatever object it left and its row.
    pending_expiry: timedelta = timedelta(days=1)
    # A deleted file's object and row are erased this long after the delete.
    # The row stops counting at the delete; the day is for a person who
    # removed the wrong file to be helped by an operator.
    retention: timedelta = timedelta(days=1)
    purge_batch: int = 100  # rows (and objects) one sweep erases per tenant


class MediaManagerImpl(MediaManagerInterface):
    def __init__(
        self,
        storage: MediaStorageInterface,
        buckets: BucketsInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        flags: FlagsInterface,
        options: MediaOptions,
    ) -> None:
        self._storage = storage
        self._buckets = buckets
        self._tenancy = tenancy
        self._relay = relay
        self._flags = flags
        self._options = options

    async def create_file(self, ctx: TenantContext, file: File) -> File:
        ctx.require(Permission.WRITE)
        flags = await self._flags.evaluate(ctx.org_id, ctx.user_id)
        if not flags.on(Flag.MEDIA_UPLOADS):
            raise FeatureOff(f"{Flag.MEDIA_UPLOADS} is off: a new upload is refused")
        refusal = upload_refusal(
            file.purpose, file.name, file.content_type, file.size_bytes, file.subject_id
        )
        if refusal is not None:
            raise ValidationFailed(refusal)
        now = utcnow()
        key = object_key(file.purpose, file.id)
        # The store's upload first, then the row that names it: a crash
        # between the two leaves an upload no row names, which the bucket's
        # lifecycle aborts; the other order would leave a row with no upload.
        upload_id = await self._buckets.start_upload(
            ctx.org_id, BUCKET, key, file.content_type, deadline=ctx.deadline
        )
        created = file.model_copy(
            update={
                "key": key,
                "extension": extension_of(file.name),
                "status": FileStatus.PENDING,
                "upload_id": upload_id,
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "deleted_at": None,
                "deleted_by": None,
            }
        )
        rows = (outbox_row(ctx, "media.file.created", created.id, {}),)  # ids only
        if not await self._storage.create_file(ctx.org_id, created, rows):
            # A retry under the same id: the row as stored is the answer, and
            # the upload this attempt started is no one's.
            await self._buckets.abort_upload(ctx.org_id, BUCKET, key, upload_id)
            existing = await self._storage.read_file(ctx.org_id, created.id)
            if existing is None:
                raise TenantMismatch(f"file {created.id} is not in {ctx.org_id}")
            return existing
        await self._relay.relay_all(ctx.org_id, rows)
        return created

    async def issue_upload(self, ctx: TenantContext, file_id: UUID) -> IssuedUpload:
        ctx.require(Permission.WRITE)
        file = await self._pending(ctx, file_id)
        upload_id = self._upload_id(file)
        plan = part_sizes(file.size_bytes)
        held = await self._buckets.list_parts(
            ctx.org_id, BUCKET, file.key, upload_id, len(plan), deadline=ctx.deadline
        )
        have = {part.number: part.size_bytes for part in held}
        offsets = (0, *accumulate(plan))
        missing = [n for n, size in enumerate(plan, 1) if have.get(n) != size]
        ttl = self._options.upload_ttl
        parts = [
            IssuedPart(
                number=n,
                offset=offsets[n - 1],
                size_bytes=plan[n - 1],
                url=await self._buckets.presign_part(
                    ctx.org_id, BUCKET, file.key, upload_id, n, plan[n - 1], ttl
                ),
            )
            for n in missing[: self._options.parts_per_issue]
        ]
        return IssuedUpload(
            upload_id=upload_id,
            parts=tuple(parts),
            remaining=len(missing),
            expires_at=utcnow() + ttl,
        )

    async def confirm_file(self, ctx: TenantContext, file_id: UUID) -> File:
        ctx.require(Permission.WRITE)
        file = await self.get_file(ctx, file_id)
        if file.status is FileStatus.STORED:
            return file
        self._own(ctx, file)
        try:
            # Refused, with nothing landing, unless the parts are the size's plan.
            await self._buckets.complete_upload(
                ctx.org_id,
                BUCKET,
                file.key,
                self._upload_id(file),
                file.size_bytes,
                deadline=ctx.deadline,
            )
        except InfraException as error:
            # The store holds no such upload. A complete that landed before its
            # row was written is found by its object; with no object, there is
            # no upload under way. Any other refusal stands as it is.
            if error.http_status != 404:
                raise
            if not await self._buckets.exists(ctx.org_id, BUCKET, file.key, deadline=ctx.deadline):
                raise ValidationFailed(f"file {file_id} has no upload under way") from error
        stored = file.model_copy(
            update={"status": FileStatus.STORED, "updated_at": utcnow(), "updated_by": ctx.user_id}
        )
        await self._write(ctx, stored, "updated")
        return stored

    async def get_file(self, ctx: TenantContext, file_id: UUID) -> File:
        ctx.require(Permission.READ)
        file = await self._storage.read_file(ctx.org_id, file_id)
        if file is None or file.deleted_at is not None:
            raise NotFound(f"file {file_id} not found")
        return file

    async def get_files(
        self,
        ctx: TenantContext,
        purpose: FilePurpose,
        subject_id: UUID | None,
        after: UUID | None,
        limit: int,
    ) -> FilePage:
        ctx.require(Permission.READ)
        limit = max(1, min(limit, self._options.max_limit))
        rows = await self._storage.read_files(
            ctx.org_id, purpose, subject_id, FileStatus.STORED, after, limit + 1
        )
        return FilePage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def issue_download(
        self, ctx: TenantContext, file_id: UUID, *, inline: bool = False
    ) -> DownloadLink:
        file = await self._stored(ctx, file_id)
        ttl = self._options.download_ttl
        url = await self._buckets.presign_get(
            ctx.org_id,
            BUCKET,
            file.key,
            ttl,
            content_type=file.content_type,
            content_disposition=content_disposition(file.name, inline),
        )
        return DownloadLink(url=url, expires_at=utcnow() + ttl)

    async def delete_file(self, ctx: TenantContext, file_id: UUID) -> File:
        ctx.require(Permission.WRITE)
        file = await self.get_file(ctx, file_id)
        return await self._delete(ctx, file)

    async def delete_subject_files(
        self, ctx: TenantContext, purpose: FilePurpose, subject_id: UUID
    ) -> int:
        ctx.require(Permission.WRITE)
        deleted = 0
        while True:
            # Each page is read from the top: the rows it deletes leave the
            # live set, so the next read starts past them without a cursor.
            page = await self._storage.read_files(
                ctx.org_id, purpose, subject_id, None, None, self._options.max_limit
            )
            for file in page:
                await self._delete(ctx, file)
            deleted += len(page)
            if len(page) < self._options.max_limit:
                return deleted

    async def get_usage(self, ctx: TenantContext) -> StorageUsage:
        ctx.require(Permission.READ)
        return await self._storage.read_usage(ctx.org_id)

    async def purge_across_tenants(self) -> int:
        now = utcnow()
        files = await self._storage.read_purgeable(
            deleted_before=now - self._options.retention,
            pending_before=now - self._options.pending_expiry,
            limit=self._options.purge_batch,
        )
        # The objects first, then the rows: a failure between the two leaves a
        # row whose object is gone, and the next sweep deletes the object again,
        # which the store answers as done. The other order would leave an
        # object no row names, which nothing would ever find.
        for org_id, file in files:
            await self._remove_object(org_id, file)
        return await self._storage.purge_files_across_tenants([f.id for _, f in files])

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        # The tenant keeps nothing but its org row: every file goes, live or
        # not, a batch per sweep, the objects first as above.
        files = await self._storage.read_every_file(ctx.org_id, None, self._options.purge_batch)
        for file in files:
            await self._remove_object(ctx.org_id, file)
        return await self._storage.purge_files(ctx.org_id, [f.id for f in files])

    async def _remove_object(self, org_id: UUID, file: File) -> None:
        """What a file keeps in the store: its object, and while it is pending
        the parts of its upload, which go at once rather than with the
        bucket's lifecycle. The store answers either one already gone as
        done."""
        if file.status is FileStatus.PENDING and file.upload_id is not None:
            await self._buckets.abort_upload(org_id, BUCKET, file.key, file.upload_id)
        await self._buckets.delete(org_id, BUCKET, file.key)

    async def _pending(self, ctx: TenantContext, file_id: UUID) -> File:
        file = await self.get_file(ctx, file_id)
        self._own(ctx, file)
        if file.status is not FileStatus.PENDING:
            raise ValidationFailed(f"file {file_id} is already stored")
        return file

    async def _stored(self, ctx: TenantContext, file_id: UUID) -> File:
        file = await self.get_file(ctx, file_id)
        if file.status is not FileStatus.STORED:
            raise ValidationFailed(f"file {file_id} has not been uploaded")
        return file

    @staticmethod
    def _upload_id(file: File) -> str:
        """The store's upload of a pending file. A row that names none has no
        upload under way to sign or complete, and the sweep removes it."""
        if file.upload_id is None:
            raise ValidationFailed(f"file {file.id} has no upload under way")
        return file.upload_id

    @staticmethod
    def _own(ctx: TenantContext, file: File) -> None:
        """An upload is its starter's: nobody else signs its parts or
        confirms it."""
        if file.created_by != ctx.user_id:
            raise NotAuthorized(f"file {file.id} is being uploaded by someone else")

    async def _delete(self, ctx: TenantContext, file: File) -> File:
        now = utcnow()
        deleted = file.model_copy(
            update={
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        await self._write(ctx, deleted, "deleted")
        return deleted

    async def _write(self, ctx: TenantContext, file: File, action: str) -> None:
        """The row and the row that announces it in one storage call, then the
        relay at once; the sweep catches what a crash left behind."""
        rows = (outbox_row(ctx, f"media.file.{action}", file.id, {}),)
        await self._storage.write_file(ctx.org_id, file, rows)
        await self._relay.relay_all(ctx.org_id, rows)
