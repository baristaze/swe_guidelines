"""What moves bytes: a form the uploader posts straight to the store, and a
link the downloader follows. Either may come back without a URL, when the
store cannot presign; the bytes then go through the API's own content route,
held to the same bounds."""

from datetime import datetime

from acme.om.base import Platform


class UploadForm(Platform):
    """A form POST of `fields`, in order, then the file, to `url`. The signed
    policy carries the file's content type and its size as the upper bound."""

    url: str | None
    fields: tuple[tuple[str, str], ...] = ()
    expires_at: datetime


class DownloadLink(Platform):
    url: str | None
    expires_at: datetime
