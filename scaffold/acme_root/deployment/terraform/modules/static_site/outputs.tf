output "bucket_name" {
  description = "Where the deploy uploads the built site."
  value       = aws_s3_bucket.this.id
}

output "distribution_id" {
  description = "Invalidated after each upload of the entry points."
  value       = aws_cloudfront_distribution.this.id
}

output "distribution_domain_name" {
  description = "The alias target for the site's domain name."
  value       = aws_cloudfront_distribution.this.domain_name
}

output "distribution_zone_id" {
  description = "CloudFront's hosted zone, for the alias record."
  value       = aws_cloudfront_distribution.this.hosted_zone_id
}

output "url" {
  description = "Where the site answers."
  value       = "https://${var.domain_name}"
}
