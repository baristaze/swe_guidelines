# Files

The files an org keeps: a document on a task, a picture, a recording.
This is one of the kinds of thing [Acme is made of](../../../../README.md).
Other parts of Acme use it for their own files; a task's attachments
are the first.

## The nouns

- **File**: a record of one file in the store, never the file itself.
  It holds the name the file had on the uploader's computer, its
  extension, its type, its size in bytes, who uploaded it, and its
  purpose. The bytes live in a separate store, in the org's own part of
  it.
- **Purpose**: why the file is there. A *task attachment* belongs to
  one task, which the file names. A *voice dictation* is a recording
  that belongs to nothing yet. A *task import* is a CSV file of tasks
  to import; it belongs to nothing, and the import that reads it names
  it.
- **Status**: *pending* while the upload is under way, *stored* once
  the file has arrived in the store.
- **Storage used**: what the org keeps, counted from its files: how
  many and how many bytes, per purpose, plus the uploads under way.

## What can happen

- **Start an upload.** A file record is made, pending. Nothing moves
  yet. Only the part of Acme the file belongs to starts one: a task
  attachment is started on the task.
- **Upload.** The person who started the upload gets a form that lets
  their browser send the bytes straight to the store, for a few
  minutes, for this one file. Where the store cannot take a form, the
  bytes go through Acme instead.
- **Confirm.** The upload is done: Acme looks for the file in the
  store, and only when it is there does the file become stored.
- **List** a subject's stored files, oldest first, a page at a time.
- **Preview and download.** A link that works for a few minutes, for
  this one file: shown in the page for a preview, under the file's own
  type, or saved under the file's own name for a download.
- **Remove.** The file is hidden at once and stops counting toward the
  storage used.
- **Sweep.** A removed file is erased from the store, then its record,
  a day after it was removed. An upload started and never confirmed is
  erased the same way a day after it started. When an org is deleted
  and its retention has passed, every file of the org goes.

## The rules

- **Every file belongs to one org.** Another org's file is answered the
  way a file that never existed is, and its bytes sit in another part
  of the store.
- **An upload is bounded before it starts.** Each purpose names the
  types it accepts and the largest file it takes. The type must be one
  of those, the name's extension must fit the type, and the name is a
  name, not a path. A task attachment (an image, a document, a sound, or a video) is at
  most 100 MB; a voice
  dictation at most 10 MB; a task import, a `.csv` file of type
  `text/csv`, at most 1 MB. The numbers are illustrative.
- **The store holds the upload to its bounds.** The form names the type
  and the size, and the store refuses a file of another type or a
  larger one. The recorded size is the one the upload named, which is
  the most the store let in.
- **Only the uploader finishes an upload.** Nobody else gets its form
  or confirms it.
- **Some fields are never the caller's.** Where the file lives, its
  extension, and its status are set by Acme.
- **Storage used is counted, not enforced here.** A plan's limit is
  held against the same count.
