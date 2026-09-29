output "prefix" {
  value = var.prefix
}

output "bucket_names" {
  value = { for name, bucket in aws_s3_bucket.this : name => bucket.bucket }
}

output "origins" {
  description = "Per bucket, the origins a presigned URL of it may name: the regional endpoint and the global one."
  value = {
    for name, bucket in aws_s3_bucket.this : name => [
      "https://${bucket.bucket_regional_domain_name}",
      "https://${bucket.bucket}.s3.amazonaws.com",
    ]
  }
}

output "policy_arn" {
  description = "Attached to every task role that reads or writes blobs."
  value       = aws_iam_policy.use.arn
}
