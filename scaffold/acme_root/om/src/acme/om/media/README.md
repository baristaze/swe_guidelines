# Files

The files an org keeps, as references to objects in the store. This is
one of the kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **File**: a record of one object in the store, never the bytes: the
  name it had on the uploader's computer, its extension, its type, its
  size, who uploaded it, its purpose, and the subject it belongs to,
  when its purpose has one. The bytes live under the org's own prefix
  in the store.
- **Purpose**: why the file is there, which decides its bounds and what
  its subject names. The core has one, `upload`: a file a member keeps
  in the org, with no subject, of at most 100 MB, an image, a document,
  a sound, or a video.
- **Status**: `pending` while the upload is under way, `stored` once
  the object is in the store.
- **Storage used**: the org's files counted per purpose, stored and
  pending.

## What can happen

- **Start an upload** (`POST /v1/media/files`). The store starts a
  multipart upload of the file, and a pending record is made that names
  it. No bytes move yet.
- **Upload** (`POST /v1/media/files/{id}/upload`). The uploader gets the
  parts the store does not hold yet, each a link good for a few minutes
  and for one part of this one file, and sends each part straight to the
  store. Asked again, Acme signs only the parts still missing, under the
  same upload, so an upload cut off resumes where it stopped. No route
  of the API takes a file's bytes.
- **Confirm.** The store puts the parts together into the file; only
  then is the file stored.
- **List** a purpose's or a subject's stored files, oldest first, a
  page at a time (`GET /v1/media/files`).
- **Download** through a link that works for a few minutes, under the
  file's own name and type, straight from the store.
- **Remove.** The file is hidden at once and stops counting.
- **Sweep.** A removed file's object goes, then its record, a day
  later. An upload never confirmed goes a day after it started, with
  every part the store held of it. A purged org's files all go.

## The rules

- **Every file belongs to one org.** Another org's file answers as one
  that never existed, and its bytes sit under another prefix.
- **An upload is bounded before it starts.** The type must be one its
  purpose accepts, the extension must fit the type, the name is a name
  and never a path, and the size is within the purpose's ceiling. The
  numbers are illustrative.
- **The store holds the upload to its bounds.** Each part's link is
  signed for that part's length, and the store refuses a longer one. The
  confirm is refused unless the parts add up to the declared size, and
  nothing lands then.
- **Only the uploader finishes an upload.**
- **Some fields are never the caller's**: the key, the extension, and
  the status are Acme's.

## How another namespace composes it

A namespace that keeps files adds a purpose to `FilePurpose` with its
bounds in `media.rules.BOUNDS`, and names its own record as the file's
`subject_id`. It starts an upload through `create_file` from its own
route, reads a subject's files with `get_files`, and calls
`delete_subject_files` when the subject goes. Media knows nothing of
what a subject is. A limit on storage is held against `get_usage`.
