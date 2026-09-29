"""What a file list answers with: one page, and whether another follows.
The manager asks storage for one row more than the page and keeps it out."""

from acme.om.base import Platform
from acme.om.media.types.file import File


class FilePage(Platform):
    items: tuple[File, ...]
    has_more: bool
