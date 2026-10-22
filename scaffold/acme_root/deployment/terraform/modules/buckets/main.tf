# One private, versioned, encrypted bucket per enum member. Tenant isolation
# is the impl's key prefix; nothing here is public.

locals {
  tags = { "acme:environment" = var.environment }
}

resource "aws_s3_bucket" "this" {
  for_each = toset(var.buckets)

  bucket = "${var.prefix}-${each.key}"
  # A destroy of a bucket with objects in it fails, which is right until the
  # nuke; the nuke's apply turns this on first, so its destroy empties them.
  force_destroy = var.destroyable
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# What leaves a bucket on its own. A delete in a versioned bucket keeps the
# previous version, so that version expires after a while and the delete
# marker it leaves goes with it. Every upload is multipart, and its parts are
# billed until it completes or is aborted: the maintenance sweep aborts an
# upload never confirmed a day after it started, and this rule aborts any
# upload still incomplete a day after it began, one the sweep never saw
# among them, so no part is kept beyond it.
resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    id     = "expire-what-was-deleted"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_days
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}

# A browser PUTs each part of an upload to a browser bucket by its presigned
# URL and follows a presigned link from it (a download, or a preview: an
# image, a video in ranges, a sound, a PDF), from the portal's origin: the two
# methods, from those origins only. The URL and the link carry the authority;
# this only lets the page send the part and read the answer.
resource "aws_s3_bucket_cors_configuration" "this" {
  for_each = length(var.browser_origins) == 0 ? toset([]) : toset(var.browser_buckets)

  bucket = aws_s3_bucket.this[each.key].id

  cors_rule {
    allowed_methods = ["PUT", "GET"]
    allowed_origins = var.browser_origins
    allowed_headers = ["*"]
    # A player reads a video in ranges; the page may read how much it got.
    expose_headers  = ["ETag", "Accept-Ranges", "Content-Range", "Content-Length"]
    max_age_seconds = 3000
  }
}

data "aws_iam_policy_document" "use" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [for bucket in aws_s3_bucket.this : bucket.arn]
  }

  # A presigned part or link acts as the role that signed it, for the one
  # operation it names, so this is also everything a browser holding one can
  # reach, and a bucket that names a key pattern is granted that pattern and
  # no more. PutObject covers a multipart upload's start, its parts, and its
  # complete; the service lists an upload's parts and aborts an abandoned one.
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListMultipartUploadParts",
      "s3:AbortMultipartUpload",
    ]
    resources = [
      for name, bucket in aws_s3_bucket.this : "${bucket.arn}/${lookup(var.object_key_patterns, name, "*")}"
    ]
  }
}

resource "aws_iam_policy" "use" {
  name   = "acme-${var.environment}-buckets"
  policy = data.aws_iam_policy_document.use.json
  tags   = local.tags
}
