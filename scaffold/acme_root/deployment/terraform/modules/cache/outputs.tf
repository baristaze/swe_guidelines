output "url" {
  description = "ACME_VALKEY_URL for every process."
  value       = "valkeys://${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"
}

output "member_clusters" {
  description = "The cache cluster ids the replication group is made of; the dimension their CloudWatch metrics carry."
  value       = aws_elasticache_replication_group.this.member_clusters
}
