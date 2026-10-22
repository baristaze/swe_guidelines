# ADR 0107: A blob crosses by a presigned URL, and every upload is multipart

**Status**: accepted (2026-10-22)

## Context

A file moves between a client and a bucket: a member's upload, a
download, a preview. Its size runs from a few bytes to the ceiling its
purpose names, and a product raises that ceiling for its own kinds of
file.

A transfer a service relays holds the service's memory and one of its
request slots for as long as the bytes take on the client's link. A
transfer the store takes by one signed request starts over from zero
when the link drops near its end, and the client holds the whole file to
send it.

A multipart upload is the store's own resumable transfer. Each part is
a request of its own, the store keeps every part it took until the
upload completes or is aborted, and it lists them on request. A part
the store keeps is billed, so an upload left incomplete costs until
something aborts it.

## Decision

**Every blob that crosses between a client and a bucket goes by a
presigned URL, whatever its size.** No route of a service takes or
serves a blob's bytes, and no size threshold sends small files another
way. A download is a presigned GET. A service's own write, such as a
worker's export, is a `put`, and is not a client's transfer.

**Every upload is multipart.** `POST /v1/media/files` starts the
store's upload for the declared size and type, and the file's row keeps
the upload id. `POST .../upload` lists the parts the store holds and
signs the missing ones, a window of at most `parts_per_issue`, each for
its own number and length, with its offset in the file. The client
sends each part straight to the store. Asked again after a cut, the
route signs only what is still missing, under the same upload id, so an
upload resumes where it stopped. `POST .../confirm` completes the
upload from the store's own list of its parts.

**The store holds the bounds.** A part's URL is SigV4 with
`Content-Length` among its signed headers, so the store refuses a body
of any other length. `part_sizes` plans the parts from the size alone,
8 MiB each, or the larger whole MiB that keeps an upload within 10,000
parts. The complete is refused unless the parts the store holds are
that plan, and so sum to the declared size, and then nothing lands.

**Every backend signs.** No presign answers `None`. The local impl,
which the unit suite runs on, signs its URLs with a key of its own and
answers them with a store app of its own (`BucketsLocalImpl.store`),
which a test mounts as the store's host and no route serves. The local
stack runs MinIO, which the S3 impl reaches.

**Nothing incomplete is kept forever.** The sweep aborts the upload of
a file never confirmed when it removes the file, a day after it started,
and every bucket aborts an upload still incomplete a day after it began
(`abort_incomplete_multipart_upload`, in
[`buckets/main.tf`](../../deployment/terraform/modules/buckets/main.tf)).

## Consequences

A service's memory and request slots are bounded whatever the size of a
file, and a dropped link costs the part in flight. The clients carry
the loop: the Python client and the command line read the file a chunk
at a time, and `acme upload --resume <id>` goes on with an upload that
stopped; the TypeScript client's `uploadFile` slices the file the same
way in a browser.

An upload is at least three calls to the API: the start, one issue per
window of parts, and the confirm. A browser bucket answers a `PUT`
preflight from the portal's origin.

A test that moves bytes sends them to the local store through its
transport, never through the app.
