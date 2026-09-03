# ── The evidence vault ──────────────────────────────────────────────────────
# This is the part that lets the word "immutable" survive a reviewer.
#
# The reviewer's finding was exact: a hash chain proves the rows that remain
# were not altered; it cannot prove that rows which once existed still do. A
# restored snapshot with its tail missing verifies as a valid, shorter chain.
#
# So every committed event, and every chaos-run evidence bundle, is written
# HERE - to an S3 bucket with Object Lock in COMPLIANCE mode - and the write
# is part of the governed transition, not a best-effort afterthought. In
# Compliance mode no principal in the account, root included, can delete or
# overwrite an object before its retention expires. The only way to make
# evidence disappear is to close the AWS account, and even that has a delay.
#
# That is a stronger contract than the ledger alone can make, and it is the
# one the demo's tamper pavilion proves live.

resource "aws_kms_key" "evidence" {
  description             = "${var.name} evidence vault"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "evidence" {
  name          = "alias/${var.name}-evidence"
  target_key_id = aws_kms_key.evidence.key_id
}

resource "random_id" "bucket" {
  byte_length = 4
}

resource "aws_s3_bucket" "evidence" {
  bucket = "${var.name}-evidence-${random_id.bucket.hex}"

  # Object Lock can only be enabled at creation. It cannot be added later and
  # it cannot be turned off. Deliberate.
  object_lock_enabled = true

  # Terraform will refuse to destroy a non-empty locked bucket, which is the
  # correct behaviour for an evidence vault. Teardown of the demo leaves this
  # bucket standing until retention elapses.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.evidence_retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.evidence]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.evidence.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Belt over the Object Lock braces: deny plaintext transport and deny any
# attempt to weaken the lock configuration itself.
data "aws_iam_policy_document" "evidence_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [aws_s3_bucket.evidence.arn, "${aws_s3_bucket.evidence.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
  statement {
    sid    = "DenyLockWeakening"
    effect = "Deny"
    actions = [
      "s3:PutBucketObjectLockConfiguration",
      "s3:PutBucketVersioning",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [aws_s3_bucket.evidence.arn]
  }
}

resource "aws_s3_bucket_policy" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  policy = data.aws_iam_policy_document.evidence_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.evidence]
}

# ── Status page: the pedestrian level ───────────────────────────────────────
# Visitors see this. It is the only public-read bucket, it holds nothing
# sensitive, and it is rewritten by the conductor after every run.

resource "aws_s3_bucket" "status" {
  bucket        = "${var.name}-status-${random_id.bucket.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_website_configuration" "status" {
  bucket = aws_s3_bucket.status.id
  index_document {
    suffix = "index.html"
  }
}

resource "aws_s3_bucket_public_access_block" "status" {
  bucket                  = aws_s3_bucket.status.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

data "aws_iam_policy_document" "status_public_read" {
  statement {
    effect  = "Allow"
    actions = ["s3:GetObject"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = ["${aws_s3_bucket.status.arn}/*"]
  }
}

resource "aws_s3_bucket_policy" "status" {
  bucket     = aws_s3_bucket.status.id
  policy     = data.aws_iam_policy_document.status_public_read.json
  depends_on = [aws_s3_bucket_public_access_block.status]
}
