"""What moves bytes, always straight between the client and the store: the
parts of an upload, each signed for its own number and length, and the link
a download follows. No route of the API takes or serves a file's bytes."""

from datetime import datetime

from acme.om.base import OmBase


class IssuedPart(OmBase):
    """One part to send: the `size_bytes` bytes of the file from `offset`,
    as the body of a PUT to `url`, whose signature holds it to that
    length."""

    number: int
    offset: int
    size_bytes: int
    url: str


class IssuedUpload(OmBase):
    """The parts of an upload the store does not hold yet, a window of them
    at a time, under the upload's one id. `remaining` counts every part
    still missing, these included; none means the upload is ready to
    confirm. Asked again, it signs what is still missing, so an upload cut
    off resumes where it stopped."""

    upload_id: str
    parts: tuple[IssuedPart, ...]
    remaining: int
    expires_at: datetime


class DownloadLink(OmBase):
    url: str
    expires_at: datetime
